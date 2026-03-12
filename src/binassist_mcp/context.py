"""
Binary context management for BinAssistMCP

This module provides context management for multiple Binary Ninja BinaryViews
with automatic name deduplication and lifecycle management.
"""

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from pathlib import Path

from .logging import log

try:
    import binaryninja as bn
    from binaryninja import AnalysisState
    BINJA_AVAILABLE = True
except ImportError:
    BINJA_AVAILABLE = False
    AnalysisState = None
    log.log_warn("Binary Ninja not available")


@dataclass
class BinaryInfo:
    """Information about a loaded binary"""
    name: str
    view: Optional[object]  # bn.BinaryView when available
    file_path: Optional[Path] = None
    load_time: Optional[float] = None
    analysis_complete: bool = False
    
    def __post_init__(self):
        if self.file_path and isinstance(self.file_path, str):
            self.file_path = Path(self.file_path)


class BinAssistMCPBinaryContextManager:
    """Context manager for multiple Binary Ninja BinaryViews"""
    
    def __init__(self, max_binaries: int = 10):
        """Initialize the context manager

        Args:
            max_binaries: Maximum number of binaries to keep loaded
        """
        self.max_binaries = max_binaries
        self._binaries: Dict[str, BinaryInfo] = {}
        self._name_counter: Dict[str, int] = {}
        self._lock = threading.RLock()  # Thread safety for binary operations
        
    def add_binary(self, binary_view: object, name: Optional[str] = None) -> str:
        """Add a BinaryView to the context with automatic name deduplication

        Args:
            binary_view: The BinaryView to add
            name: Optional name to use (defaults to filename)

        Returns:
            The name used for the BinaryView
        """
        if not BINJA_AVAILABLE:
            raise RuntimeError("Binary Ninja not available")

        if name is None:
            name = self._extract_name(binary_view)

        # Sanitize name for URL usage
        sanitized_name = self._sanitize_name(name)

        with self._lock:
            # Deduplicate name if needed
            unique_name = self._get_unique_name(sanitized_name)

            # Check if we need to evict old binaries
            if len(self._binaries) >= self.max_binaries:
                self._evict_oldest_binary()

            # Add binary info
            binary_info = BinaryInfo(
                name=unique_name,
                view=binary_view,
                file_path=self._get_file_path(binary_view),
                load_time=time.time(),
                analysis_complete=self._is_analysis_complete(binary_view)
            )

            self._binaries[unique_name] = binary_info
            log.log_info(f"Added binary '{unique_name}' to context (total: {len(self._binaries)})")

            return unique_name
        
    def open_binary(self, file_path: str, bndb_path: Optional[str] = None,
                    wait_for_analysis: bool = True) -> Tuple[str, dict]:
        """Open a binary file or existing .bndb database in Binary Ninja.

        Uses the Binary Ninja UI to open the file (via UIContext.openFilename
        dispatched on the main thread), then syncs the context manager with
        the UI to pick up the new BinaryView. Falls back to headless bn.load()
        if no UI is available.

        For raw binaries (non-.bndb files): bndb_path is required. After
        analysis completes, the analyzed database is saved to bndb_path.

        For existing .bndb files: bndb_path is ignored — the database is
        already on disk and will be opened directly.

        Args:
            file_path: Path to the binary file or .bndb database to open
            bndb_path: Path where the .bndb database file should be saved
                       after analysis completes. Required for raw binaries,
                       ignored for .bndb files. The LLM should ask the user
                       for the desired save location before calling this tool.
            wait_for_analysis: Whether to wait for Binary Ninja's initial analysis
                               to complete before returning (default True).
                               Set to False for faster return on large binaries.

        Returns:
            Tuple of (binary_name, status_dict) where status_dict contains:
            - file_path: Path of the opened file
            - bndb_path: Path of the .bndb database (saved or existing)
            - analysis_complete: Whether analysis finished
            - function_count: Number of functions discovered
            - error: Error message if any step failed (partial success possible)

        Raises:
            RuntimeError: If Binary Ninja is not available
            FileNotFoundError: If the file_path does not exist
            ValueError: If the file could not be loaded by Binary Ninja,
                        or if bndb_path is missing for a raw binary
        """
        if not BINJA_AVAILABLE:
            raise RuntimeError("Binary Ninja not available")

        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        resolved_path = str(path.resolve())
        is_bndb = path.suffix.lower() == '.bndb'

        # bndb_path is required for raw binaries, ignored for .bndb files
        if not is_bndb and (not bndb_path or not bndb_path.strip()):
            raise ValueError(
                "bndb_path is required when opening a raw binary — you must "
                "specify where to save the analyzed .bndb database. Ask the "
                "user for the desired save location."
            )

        status = {
            "file_path": resolved_path,
            "bndb_path": resolved_path if is_bndb else None,
            "analysis_complete": False,
            "function_count": 0,
            "error": None,
        }

        log.log_info(f"Opening {'database' if is_bndb else 'binary'}: {file_path}")

        # Determine if the UI is available
        ui_available = False
        try:
            from binaryninjaui import UIContext
            if UIContext.allContexts():
                ui_available = True
        except ImportError:
            pass

        bv = None

        if ui_available:
            # ----- UI mode: open via UIContext on the main thread -----
            from binaryninja.mainthread import execute_on_main_thread_and_wait, is_main_thread

            open_result = [False]

            def _open_in_ui():
                try:
                    ctx = UIContext.allContexts()
                    if ctx:
                        open_result[0] = ctx[0].openFilename(resolved_path)
                except Exception as e:
                    log.log_warn(f"UIContext.openFilename failed: {e}")
                    open_result[0] = False

            log.log_info("Opening binary via UIContext.openFilename on main thread...")
            if is_main_thread():
                _open_in_ui()
            else:
                execute_on_main_thread_and_wait(_open_in_ui)

            if not open_result[0]:
                raise ValueError(
                    f"Binary Ninja UI failed to open '{file_path}' — "
                    "unsupported format, corrupt file, or no UI context available"
                )

            log.log_info(f"UI opened {file_path}, waiting for analysis...")

            # Wait for BN to finish loading and analyzing the binary.
            # The UI opens asynchronously; we need to poll until the view
            # appears in the UI tabs and (optionally) analysis completes.
            max_wait = 120  # seconds
            poll_interval = 0.5
            elapsed = 0.0

            while elapsed < max_wait:
                time.sleep(poll_interval)
                elapsed += poll_interval

                # Sync to discover the newly opened view
                self.sync_with_binja()

                # Look for the binary we just opened by matching file path
                with self._lock:
                    for name, info in self._binaries.items():
                        if info.file_path and str(info.file_path) == resolved_path:
                            bv = info.view
                            break

                if bv is not None:
                    if not wait_for_analysis:
                        break
                    # Check if analysis is done
                    if self._is_analysis_complete(bv):
                        log.log_info(f"Analysis complete for {file_path}")
                        break

            if bv is None:
                raise ValueError(
                    f"Binary '{file_path}' was opened in the UI but did not appear "
                    f"in context after {max_wait}s — this should not happen"
                )

        else:
            # ----- Headless mode: fall back to bn.load() -----
            log.log_info("No UI available, loading binary headlessly via bn.load()...")
            try:
                bv = bn.load(resolved_path)
            except Exception as e:
                raise ValueError(f"Binary Ninja failed to load '{file_path}': {e}")

            if bv is None:
                raise ValueError(
                    f"Binary Ninja returned None for '{file_path}' — "
                    "unsupported format or corrupt file"
                )

            log.log_info(f"Binary loaded headlessly: {file_path} (arch={getattr(bv, 'arch', 'unknown')})")

            if wait_for_analysis:
                try:
                    log.log_info(f"Waiting for analysis to complete on {file_path}...")
                    bv.update_analysis_and_wait()
                    log.log_info(f"Analysis complete for {file_path}")
                except Exception as e:
                    log.log_warn(f"Analysis wait failed (non-fatal): {e}")
                    status["error"] = f"Analysis wait failed: {e}"

            # Add to context manager (in UI mode, sync_with_binja already did this)
            self.add_binary(bv)

        # --- Common post-open logic ---

        status["analysis_complete"] = self._is_analysis_complete(bv)

        # Count functions
        try:
            status["function_count"] = len(list(bv.functions))
        except Exception:
            pass

        # Save analyzed database to .bndb (only for raw binaries, not .bndb files)
        if not is_bndb and bndb_path:
            bndb = Path(bndb_path)
            try:
                bndb.parent.mkdir(parents=True, exist_ok=True)
                bv.create_database(str(bndb.resolve()))
                status["bndb_path"] = str(bndb.resolve())
                log.log_info(f"Saved .bndb database to {bndb_path}")
            except Exception as e:
                log.log_warn(f"Failed to save .bndb to '{bndb_path}': {e}")
                if status["error"]:
                    status["error"] += f"; bndb save failed: {e}"
                else:
                    status["error"] = f"bndb save failed: {e}"

        # Find the name this binary was registered under
        binary_name = None
        with self._lock:
            for name, info in self._binaries.items():
                if info.view is bv:
                    binary_name = name
                    break

        if binary_name is None:
            binary_name = self._extract_name(bv)

        log.log_info(
            f"Binary '{binary_name}' ready in context "
            f"(functions={status['function_count']}, "
            f"analysis_complete={status['analysis_complete']})"
        )

        return binary_name, status

    def get_binary(self, name: str) -> object:
        """Get a BinaryView by name

        Args:
            name: The name of the BinaryView

        Returns:
            The BinaryView if found

        Raises:
            KeyError: If the binary is not found
        """
        with self._lock:
            if name not in self._binaries:
                available = ", ".join(self._binaries.keys()) if self._binaries else "none"
                raise KeyError(f"Binary '{name}' not found. Available: {available}")

            binary_info = self._binaries[name]

            # Verify the binary view is still valid
            if not self._is_binary_valid(binary_info.view):
                log.log_warn(f"Binary '{name}' is no longer valid, removing from context")
                del self._binaries[name]
                raise KeyError(f"Binary '{name}' is no longer valid")

            return binary_info.view
        
    def get_binary_info(self, name: str) -> BinaryInfo:
        """Get binary information by name

        Args:
            name: The name of the binary

        Returns:
            BinaryInfo object

        Raises:
            KeyError: If the binary is not found
        """
        with self._lock:
            if name not in self._binaries:
                available = ", ".join(self._binaries.keys()) if self._binaries else "none"
                raise KeyError(f"Binary '{name}' not found. Available: {available}")

            return self._binaries[name]

    def list_binaries(self) -> List[str]:
        """List all loaded binary names

        Returns:
            List of binary names
        """
        with self._lock:
            return list(self._binaries.keys())

    def list_binary_info(self) -> Dict[str, BinaryInfo]:
        """Get information about all loaded binaries

        Returns:
            Dictionary mapping names to BinaryInfo objects
        """
        with self._lock:
            return self._binaries.copy()

    def remove_binary(self, name: str) -> bool:
        """Remove a binary from the context

        Args:
            name: Name of the binary to remove

        Returns:
            True if removed, False if not found
        """
        with self._lock:
            if name in self._binaries:
                del self._binaries[name]
                log.log_info(f"Removed binary '{name}' from context")
                return True
            return False

    def clear(self):
        """Clear all binaries from the context"""
        with self._lock:
            count = len(self._binaries)
            self._binaries.clear()
            self._name_counter.clear()
            log.log_info(f"Cleared {count} binaries from context")

    def update_analysis_status(self, name: str):
        """Update the analysis status for a binary

        Args:
            name: Name of the binary to update
        """
        with self._lock:
            if name in self._binaries:
                binary_info = self._binaries[name]
                if binary_info.view:
                    binary_info.analysis_complete = self._is_analysis_complete(binary_info.view)
                    log.log_debug(f"Updated analysis status for '{name}': {binary_info.analysis_complete}")
                
    def _extract_name(self, binary_view: object) -> str:
        """Extract name from a BinaryView"""
        if not BINJA_AVAILABLE or not binary_view:
            return "unknown"
            
        try:
            if hasattr(binary_view, 'file') and hasattr(binary_view.file, 'filename'):
                filename = binary_view.file.filename
                if filename:
                    return Path(filename).name
                    
            if hasattr(binary_view, 'name'):
                return binary_view.name
                
        except Exception as e:
            log.log_warn(f"Failed to extract name from binary view: {e}")
            
        return "unknown"
        
    def _sanitize_name(self, name: str) -> str:
        """Sanitize name for URL usage"""
        if not name:
            return "unnamed"
            
        # Replace invalid characters
        invalid_chars = '/\\:*?"<>| '
        for char in invalid_chars:
            name = name.replace(char, '_')
            
        # Remove leading/trailing dots and underscores
        name = name.strip('_.')
        
        # Ensure non-empty name
        if not name:
            name = "unnamed"
            
        return name
        
    def _get_unique_name(self, base_name: str) -> str:
        """Get a unique name by adding a counter if needed"""
        if base_name not in self._binaries:
            return base_name
            
        # Find the next available counter value
        counter = self._name_counter.get(base_name, 1)
        while True:
            unique_name = f"{base_name}_{counter}"
            if unique_name not in self._binaries:
                self._name_counter[base_name] = counter + 1
                return unique_name
            counter += 1
            
    def _get_file_path(self, binary_view: object) -> Optional[Path]:
        """Get file path from a BinaryView"""
        if not BINJA_AVAILABLE or not binary_view:
            return None
            
        try:
            if hasattr(binary_view, 'file') and hasattr(binary_view.file, 'filename'):
                filename = binary_view.file.filename
                if filename:
                    return Path(filename)
        except Exception as e:
            log.log_debug(f"Failed to get file path: {e}")
            
        return None
        
    def _is_analysis_complete(self, binary_view: object) -> bool:
        """Check if analysis is complete for a BinaryView"""
        if not BINJA_AVAILABLE or not binary_view or not AnalysisState:
            return False
            
        try:
            # Method 1: Check analysis_progress state
            if hasattr(binary_view, 'analysis_progress'):
                progress = binary_view.analysis_progress
                current_state = progress.state
                log.log_debug(f"Analysis progress state: {current_state} (IdleState={AnalysisState.IdleState})")
                # Correct API: compare state directly to AnalysisState.IdleState
                return current_state == AnalysisState.IdleState
                
            # Method 2: Check analysis_info state (alternative)
            if hasattr(binary_view, 'analysis_info'):
                info_state = binary_view.analysis_info.state
                log.log_debug(f"Analysis info state: {info_state} (IdleState={AnalysisState.IdleState})")
                return info_state == AnalysisState.IdleState
                
            # Fallback: check if we have functions
            if hasattr(binary_view, 'functions'):
                func_count = len(list(binary_view.functions))
                log.log_debug(f"Analysis status fallback: {func_count} functions found")
                return func_count > 0
                
        except Exception as e:
            log.log_debug(f"Failed to check analysis status: {e}")
            # Additional debug info
            try:
                if hasattr(binary_view, 'analysis_progress'):
                    progress = binary_view.analysis_progress
                    log.log_debug(f"Progress object type: {type(progress)}")
                    log.log_debug(f"Progress state type: {type(progress.state)}")
                    log.log_debug(f"Available AnalysisState values: {[attr for attr in dir(AnalysisState) if not attr.startswith('_')]}")
            except Exception as debug_error:
                log.log_debug(f"Failed to get debug info: {debug_error}")
            
        return False
        
    def _is_binary_valid(self, binary_view: object) -> bool:
        """Check if a BinaryView is still valid"""
        if not BINJA_AVAILABLE or not binary_view:
            return False
            
        try:
            # Try to access a basic property
            if hasattr(binary_view, 'file'):
                _ = binary_view.file
                return True
        except Exception as e:
            log.log_debug(f"Binary view validation failed: {e}")
            
        return False
        
    def _evict_oldest_binary(self):
        """Evict the oldest binary to make room for a new one"""
        if not self._binaries:
            return
            
        # Find the binary with the oldest load time
        oldest_name = None
        oldest_time = float('inf')
        
        for name, binary_info in self._binaries.items():
            if binary_info.load_time and binary_info.load_time < oldest_time:
                oldest_time = binary_info.load_time
                oldest_name = name
                
        if oldest_name:
            log.log_info(f"Evicting oldest binary '{oldest_name}' to make room")
            del self._binaries[oldest_name]
            
    def __len__(self) -> int:
        """Return the number of loaded binaries"""
        with self._lock:
            return len(self._binaries)

    def __contains__(self, name: str) -> bool:
        """Check if a binary name is in the context"""
        with self._lock:
            return name in self._binaries

    def __repr__(self) -> str:
        """String representation of the context manager"""
        with self._lock:
            return f"BinaryContextManager(binaries={len(self._binaries)}, max={self.max_binaries})"

    def sync_with_binja(self) -> dict:
        """Synchronize context with Binary Ninja's currently open views.

        Enumerates all open BinaryViews via Binary Ninja UI context,
        adds newly opened binaries to context, and removes closed/invalid
        binaries from context.

        Returns:
            Dictionary with sync status report:
            - added: list of newly added binary names
            - removed: list of removed binary names
            - unchanged: list of binaries that remained
            - synced: bool indicating if sync was performed
            - error: optional error message if sync failed
        """
        result = {
            "added": [],
            "removed": [],
            "unchanged": [],
            "synced": False,
            "error": None
        }

        if not BINJA_AVAILABLE:
            result["error"] = "Binary Ninja not available"
            return result

        # Try to access UI context for open views
        try:
            from binaryninjaui import UIContext
            ui_available = True
        except ImportError:
            ui_available = False
            log.log_debug("binaryninjaui not available, running in headless mode")

        with self._lock:
            # First, remove invalid/closed binaries from context
            names_to_remove = []
            for name, binary_info in self._binaries.items():
                if not self._is_binary_valid(binary_info.view):
                    names_to_remove.append(name)

            for name in names_to_remove:
                del self._binaries[name]
                result["removed"].append(name)
                log.log_info(f"Removed invalid/closed binary '{name}' from context")

            # If UI is available, enumerate open views and add new ones
            if ui_available:
                try:
                    # Collect all open binary views from all UI contexts
                    open_views = []

                    # Get all UI contexts (windows)
                    all_contexts = UIContext.allContexts()

                    for ctx in all_contexts:
                        if ctx is None:
                            continue

                        # Use getTabs() to enumerate all open tabs and get their BinaryViews
                        # Note: getAvailableBinaryViews() returns underlying raw views which
                        # may not have proper filenames, so we use tab-based enumeration instead
                        if hasattr(ctx, 'getTabs'):
                            tabs = ctx.getTabs()
                            for tab in (tabs or []):
                                # Each tab has getCurrentBinaryView() method
                                if hasattr(tab, 'getCurrentBinaryView'):
                                    bv = tab.getCurrentBinaryView()
                                    if bv is not None and bv not in open_views:
                                        open_views.append(bv)

                    # Build a set of file paths currently in context for comparison
                    context_paths = set()
                    for binary_info in self._binaries.values():
                        if binary_info.file_path:
                            context_paths.add(str(binary_info.file_path))

                    # Add any open views not already in context
                    for bv in open_views:
                        if bv is None:
                            continue

                        file_path = self._get_file_path(bv)
                        file_path_str = str(file_path) if file_path else None

                        # Check if this view is already in context (by path)
                        if file_path_str and file_path_str in context_paths:
                            continue

                        # Check if view object is already tracked
                        already_tracked = False
                        for binary_info in self._binaries.values():
                            if binary_info.view is bv:
                                already_tracked = True
                                break

                        if already_tracked:
                            continue

                        # Add this new binary
                        try:
                            name = self._extract_name(bv)
                            sanitized_name = self._sanitize_name(name)
                            unique_name = self._get_unique_name(sanitized_name)

                            # Check if we need to evict old binaries
                            if len(self._binaries) >= self.max_binaries:
                                self._evict_oldest_binary()

                            binary_info = BinaryInfo(
                                name=unique_name,
                                view=bv,
                                file_path=file_path,
                                load_time=time.time(),
                                analysis_complete=self._is_analysis_complete(bv)
                            )

                            self._binaries[unique_name] = binary_info
                            result["added"].append(unique_name)
                            log.log_info(f"Added newly opened binary '{unique_name}' to context")
                        except Exception as add_error:
                            log.log_warn(f"Failed to add binary view to context: {add_error}")
                except Exception as ui_error:
                    result["error"] = f"UI context error: {ui_error}"

            # Record unchanged binaries
            for name in self._binaries.keys():
                if name not in result["added"]:
                    result["unchanged"].append(name)

            result["synced"] = True

        return result