"""
FastMCP server implementation for BinAssistMCP

This module provides the main MCP server with SSE transport
and comprehensive Binary Ninja integration.
"""

import warnings
from contextlib import asynccontextmanager
from threading import Event, Thread
from typing import AsyncIterator, List, Optional

import asyncio
from hypercorn.config import Config as HypercornConfig
from hypercorn.asyncio import serve
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings

# Suppress ResourceWarnings for memory streams to reduce noise in logs
warnings.filterwarnings("ignore", category=ResourceWarning)


class ResourceManagedASGIApp:
    """ASGI app wrapper that ensures proper resource cleanup"""

    def __init__(self, app):
        self.app = app
        self._response_started = {}

    async def __call__(self, scope, receive, send):
        """ASGI callable with resource management"""
        # Track if response has started for this connection
        scope_id = id(scope)
        self._response_started[scope_id] = False

        async def wrapped_send(message):
            """Wrap send to track response state and prevent ASGI violations"""
            if message["type"] == "http.response.start":
                self._response_started[scope_id] = True
            elif message["type"] == "http.response.body":
                # Only send if response hasn't already completed
                if not self._response_started.get(scope_id):
                    log.log_debug("Attempted to send response body before response start")
                    return
            try:
                await send(message)
            except Exception as e:
                # Handle send errors gracefully (client disconnections, etc.)
                error_msg = str(e)
                # Check for expected ASGI state errors and connection issues
                if ("connection" in error_msg.lower() or
                    "closed" in error_msg.lower() or
                    "ASGIHTTPState" in error_msg or
                    "response already" in error_msg.lower() or
                    "Unexpected message type" in error_msg):
                    log.log_debug(f"Client disconnected or ASGI state error (expected): {e}")
                else:
                    log.log_warn(f"Error sending ASGI message: {e}")

        try:
            await self.app(scope, receive, wrapped_send)
        except BaseException as e:
            # Handle both exception groups and regular exceptions.
            # IMPORTANT: Request-level errors should NEVER terminate the server.
            # We catch all exceptions here, log appropriately, and return gracefully.
            import sys
            import traceback

            if sys.version_info >= (3, 11) and isinstance(e, BaseExceptionGroup):
                # Handle exception groups (Python 3.11+)
                log.log_debug(f"ASGI exception group during request: {e}")
                for exc in e.exceptions:
                    error_msg = str(exc)
                    # Check for all types of expected ASGI/connection errors
                    if ("ASGIHTTPState" in error_msg or
                        "connection" in error_msg.lower() or
                        "closed" in error_msg.lower() or
                        "response already" in error_msg.lower() or
                        "Unexpected message type" in error_msg or
                        "cancelled" in error_msg.lower() or
                        isinstance(exc, asyncio.CancelledError)):
                        log.log_debug(f"Client disconnect or ASGI state error (expected): {exc}")
                    else:
                        log.log_warn(f"Unexpected exception in request group: {exc}")
                        log.log_debug(f"Traceback: {''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))}")
                # Always return gracefully - don't let request errors kill the server
                return

            # Handle single exceptions
            error_msg = str(e)
            # Check for all types of expected ASGI/connection errors
            if ("ASGIHTTPState" in error_msg or
                "connection" in error_msg.lower() or
                "closed" in error_msg.lower() or
                "response already" in error_msg.lower() or
                "Unexpected message type" in error_msg or
                "cancelled" in error_msg.lower() or
                isinstance(e, asyncio.CancelledError)):
                log.log_debug(f"Client disconnect or ASGI state error (expected): {e}")
            else:
                # Log unexpected errors with full details, but still don't re-raise
                log.log_warn(f"Unexpected ASGI exception during request: {e}")
                log.log_debug(f"Traceback: {traceback.format_exc()}")

            # ALWAYS return gracefully - individual request failures should never
            # propagate up and terminate the server. The server should continue
            # serving other requests.
            return
        finally:
            # Clean up response tracking
            self._response_started.pop(scope_id, None)

from .config import BinAssistMCPConfig, TransportType
from .context import BinAssistMCPBinaryContextManager
from .logging import log
from .tasks import TaskManager, TaskStatus, get_task_manager
from .tools import BinAssistMCPTools

try:
    import binaryninja as bn
    BINJA_AVAILABLE = True
except ImportError:
    BINJA_AVAILABLE = False
    log.log_warn("Binary Ninja not available")


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[BinAssistMCPBinaryContextManager]:
    """Application lifecycle manager for the MCP server.

    This context manager handles the server's binary context throughout its lifetime.
    Exception handling is designed to be resilient:
    - Connection-related errors during request handling are logged but suppressed
    - The finally block only runs on actual shutdown, not on suppressed errors
    - Unrecoverable errors are re-raised to properly signal shutdown
    """
    context_manager = BinAssistMCPBinaryContextManager(
        max_binaries=getattr(server, '_config', BinAssistMCPConfig()).binary.max_binaries
    )

    # Add initial binaries if provided
    initial_binaries = getattr(server, '_initial_binaries', [])
    for binary_view in initial_binaries:
        try:
            context_manager.add_binary(binary_view)
        except Exception as e:
            log.log_error(f"Failed to add initial binary: {e}")

    log.log_info(f"Server started with {len(context_manager)} initial binaries")

    try:
        yield context_manager
    except asyncio.CancelledError:
        # CancelledError indicates graceful shutdown - don't log as error
        log.log_debug("Server lifespan received CancelledError (graceful shutdown)")
        # Re-raise to trigger finally block for cleanup
        raise
    except KeyboardInterrupt:
        # KeyboardInterrupt indicates user-initiated shutdown
        log.log_info("Server lifespan received KeyboardInterrupt")
        raise
    except BaseException as e:
        # Handle both exception groups and regular exceptions
        import sys
        import traceback

        if sys.version_info >= (3, 11) and isinstance(e, BaseExceptionGroup):
            # Check if this is an ExceptionGroup
            log.log_warn(f"Server lifespan TaskGroup error: {e}")
            all_connection_errors = True
            for exc in e.exceptions:
                error_msg = str(exc).lower()
                is_connection_error = (
                    "connection" in error_msg or
                    "closed" in error_msg or
                    "cancelled" in error_msg or
                    "ASGIHTTPState" in str(exc) or
                    isinstance(exc, asyncio.CancelledError)
                )
                if is_connection_error:
                    log.log_debug(f"Connection-related lifespan sub-exception (suppressed): {exc}")
                else:
                    log.log_error(f"Lifespan sub-exception: {exc}")
                    log.log_error(f"Traceback: {''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))}")
                    all_connection_errors = False

            if all_connection_errors:
                # All errors are connection-related - these happen during normal
                # multi-binary operation. Log and suppress, but DON'T return early.
                # Let the context manager continue running.
                log.log_debug("All lifespan exceptions are connection-related, suppressing (server continues)")
                # NOTE: We do NOT return here - that would exit the context manager
                # and trigger the finally block, shutting down the server.
                # Instead, we suppress by not re-raising.
            else:
                # Some errors are not connection-related - re-raise
                raise
        else:
            # Handle regular exceptions
            error_msg = str(e).lower()
            is_connection_error = (
                "connection" in error_msg or
                "closed" in error_msg or
                "cancelled" in error_msg
            )
            if is_connection_error:
                log.log_debug(f"Connection-related lifespan error (suppressed): {e}")
            else:
                log.log_error(f"Server lifespan error: {e}")
                log.log_error(f"Lifespan traceback: {traceback.format_exc()}")
                raise
    finally:
        try:
            log.log_info("Shutting down server, clearing binary context")
            context_manager.clear()

            # Give time for async cleanup and stream finalization
            await asyncio.sleep(0.5)

            log.log_info("Server lifespan cleanup completed")
        except Exception as e:
            log.log_error(f"Error during server shutdown: {e}")


class SSEServerThread(Thread):
    """Thread for running the SSE server with improved resource management"""
    
    def __init__(self, asgi_app, config: BinAssistMCPConfig):
        super().__init__(name="BinAssist-SSE-Server", daemon=True)
        self.asgi_app = asgi_app
        self.config = config
        self.shutdown_signal = Event()
        self.hypercorn_config = HypercornConfig()
        self.hypercorn_config.bind = [f"{config.server.host}:{config.server.port}"]

        # Configure better connection handling for resource cleanup
        self.hypercorn_config.keep_alive_timeout = 5
        self.hypercorn_config.graceful_timeout = 10
        
        # Disable hypercorn's logging to avoid ScriptingProvider messages
        self.hypercorn_config.access_log_format = ""
        self.hypercorn_config.error_logger = None
        self.hypercorn_config.access_logger = None
        
        # Completely disable hypercorn logging
        import logging
        logging.getLogger('hypercorn').disabled = True
        logging.getLogger('hypercorn.error').disabled = True
        logging.getLogger('hypercorn.access').disabled = True
        
        # Suppress resource warnings specifically for this thread
        warnings.filterwarnings("ignore", category=ResourceWarning)
        
    def run(self):
        """Run the SSE server"""
        try:
            log.log_info(f"Starting SSE server on {self.config.get_sse_url()}")
            log.log_info(f"Hypercorn config: {self.hypercorn_config.bind}")
            
            # Create a new event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            try:
                loop.run_until_complete(self._run_server())
            finally:
                loop.close()
                
        except Exception as e:
            log.log_error(f"SSE server error: {e}")
            import traceback
            log.log_error(f"SSE server traceback: {traceback.format_exc()}")
            
    async def _run_server(self):
        """Async server runner with improved resource cleanup and resilience.

        This method wraps serve() in a loop that continues on recoverable errors
        (connection errors, ASGI state errors) and only exits on explicit shutdown
        signal or unrecoverable errors.
        """
        import sys
        import traceback

        while not self.shutdown_signal.is_set():
            try:
                await serve(
                    self.asgi_app,
                    self.hypercorn_config,
                    shutdown_trigger=self._shutdown_trigger
                )
                # Normal exit from serve() means shutdown was requested
                break
            except BaseException as e:
                # Check for shutdown signal first
                if self.shutdown_signal.is_set():
                    log.log_debug("Shutdown signal set, exiting server loop")
                    break

                # Classify the exception to determine if recoverable
                is_recoverable = False
                error_details = []

                if sys.version_info >= (3, 11) and isinstance(e, BaseExceptionGroup):
                    # Handle exception groups (Python 3.11+)
                    log.log_warn(f"Server TaskGroup error (checking if recoverable): {e}")
                    all_recoverable = True
                    for exc in e.exceptions:
                        error_msg = str(exc)
                        exc_recoverable = self._is_recoverable_exception(exc, error_msg)
                        if exc_recoverable:
                            log.log_debug(f"Recoverable sub-exception: {exc}")
                        else:
                            log.log_error(f"Unrecoverable sub-exception: {exc}")
                            log.log_error(f"Traceback: {''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))}")
                            all_recoverable = False
                        error_details.append((exc, exc_recoverable))
                    is_recoverable = all_recoverable
                else:
                    # Handle single exceptions
                    error_msg = str(e)
                    is_recoverable = self._is_recoverable_exception(e, error_msg)

                    if is_recoverable:
                        log.log_debug(f"Recoverable server error: {e}")
                    else:
                        log.log_error(f"Server serve error: {e}")
                        log.log_error(f"Traceback: {traceback.format_exc()}")

                if is_recoverable:
                    # Brief pause before retry to avoid tight loop
                    log.log_info("Recoverable error encountered, server continuing...")
                    await asyncio.sleep(0.1)
                    continue
                else:
                    # Unrecoverable error, exit the loop
                    log.log_error("Unrecoverable server error, stopping server")
                    break

        # Final cleanup
        try:
            log.log_debug("Starting SSE server cleanup")
            # Allow time for all pending connections and streams to close
            await asyncio.sleep(1.0)
            log.log_debug("SSE server cleanup completed")
        except Exception as cleanup_error:
            log.log_error(f"Error during SSE server cleanup: {cleanup_error}")

    def _is_recoverable_exception(self, exc: BaseException, error_msg: str) -> bool:
        """Determine if an exception is recoverable (server should continue).

        Recoverable exceptions are typically connection-related errors that
        occur during normal operation when clients disconnect or when
        handling concurrent requests.

        Args:
            exc: The exception to check
            error_msg: String representation of the exception

        Returns:
            True if the exception is recoverable, False otherwise
        """
        # CancelledError is recoverable (client disconnect)
        if isinstance(exc, asyncio.CancelledError):
            return True

        # Check for known recoverable error patterns
        recoverable_patterns = [
            "connection",
            "closed",
            "ASGIHTTPState",
            "response already",
            "Unexpected message type",
            "client disconnect",
            "broken pipe",
            "reset by peer",
            "stream",
        ]

        error_msg_lower = error_msg.lower()
        for pattern in recoverable_patterns:
            if pattern.lower() in error_msg_lower:
                return True

        return False
            
    async def _shutdown_trigger(self):
        """Wait for shutdown signal"""
        log.log_debug("Waiting for shutdown signal")
        # Use asyncio to run the blocking wait in a thread
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self.shutdown_signal.wait)
        log.log_info("Shutdown signal received")
        
        # Allow time for existing connections to close gracefully
        await asyncio.sleep(0.5)
        
    def stop(self):
        """Stop the server with improved cleanup"""
        log.log_info("Stopping SSE server")
        self.shutdown_signal.set()
        
        # Wait for thread to finish with longer timeout for proper cleanup
        if self.is_alive():
            self.join(timeout=5.0)
            if self.is_alive():
                log.log_warn("SSE server thread did not shut down cleanly within 5 seconds")
            else:
                log.log_info("SSE server thread shutdown completed")


class BinAssistMCPServer:
    """Main BinAssistMCP server class"""
    
    def __init__(self, config: Optional[BinAssistMCPConfig] = None):
        """Initialize the MCP server
        
        Args:
            config: Configuration object, creates default if None
        """
        self.config = config or BinAssistMCPConfig()
        self.mcp_server: Optional[FastMCP] = None
        self.sse_thread: Optional[SSEServerThread] = None
        self.streamablehttp_thread: Optional[SSEServerThread] = None  # Reuse SSEServerThread for streamablehttp
        self._initial_binaries: List = []
        self._running = False
        
        log.log_info(f"Initialized BinAssistMCP server with config: {self.config}")
        
    def add_initial_binary(self, binary_view):
        """Add a binary view to be loaded on server start
        
        Args:
            binary_view: Binary Ninja BinaryView object
        """
        if not BINJA_AVAILABLE:
            log.log_warn("Binary Ninja not available, cannot add binary")
            return
            
        self._initial_binaries.append(binary_view)
        log.log_info(f"Added initial binary (total: {len(self._initial_binaries)})")
        
    def create_mcp_server(self) -> FastMCP:
        """Create and configure the FastMCP server instance"""
        try:
            log.log_info("Creating FastMCP instance...")
            mcp = FastMCP(
                name="BinAssistMCP",
#                version="1.0.0",
#                description="Comprehensive MCP server for Binary Ninja reverse engineering",
                lifespan=server_lifespan,
                # Disable DNS rebinding protection to allow binding to any IP address
                transport_security=TransportSecuritySettings(
                    enable_dns_rebinding_protection=False
                )
            )
            log.log_info("FastMCP instance created")
            
            # Store configuration and initial binaries for lifespan access
            log.log_info("Storing configuration and initial binaries...")
            mcp._config = self.config
            mcp._initial_binaries = self._initial_binaries
            
            log.log_info("Registering tools...")
            self._register_tools(mcp)
            log.log_info("Tools registered successfully")
            
            log.log_info("Registering resources...")
            self._register_resources(mcp)
            log.log_info("Resources registered successfully")

            log.log_info("Registering prompts...")
            self._register_prompts(mcp)
            log.log_info("Prompts registered successfully")

            return mcp
            
        except Exception as e:
            log.log_error(f"Failed to create MCP server: {e}")
            import traceback
            log.log_error(f"MCP server creation traceback: {traceback.format_exc()}")
            raise
        
    def _register_tools(self, mcp: FastMCP):
        """Register all MCP tools"""
        
        # Tool annotations for MCP 2025-11-25 compliance
        # readOnlyHint: tool doesn't modify state
        # idempotentHint: repeated calls produce same result
        # openWorldHint: tool may interact with external world (false for local analysis)
        READ_ONLY_ANNOTATIONS = {
            "readOnlyHint": True,
            "idempotentHint": True,
            "openWorldHint": False
        }
        MODIFY_ANNOTATIONS = {
            "readOnlyHint": False,
            "idempotentHint": True,
            "openWorldHint": False
        }

        @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
        def list_binaries(ctx: Context) -> dict:
            """List all currently loaded binary names with auto-refresh from Binary Ninja

            Automatically synchronizes with Binary Ninja's currently open views before
            returning the list. This ensures newly opened binaries are included and
            closed binaries are removed.

            Returns:
                Dictionary containing:
                - binaries: List of binary filenames currently loaded
                - sync_status: Sync operation report (added, removed, unchanged counts)
            """
            context_manager: BinAssistMCPBinaryContextManager = ctx.request_context.lifespan_context
            # Sync with Binary Ninja before returning results
            sync_result = context_manager.sync_with_binja()
            return {
                "binaries": context_manager.list_binaries(),
                "sync_status": sync_result
            }

        @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
        def get_binary_info(filename: str, ctx: Context) -> dict:
            """Get status information for a specific binary

            Args:
                filename: Name of the binary file

            Returns:
                Dictionary with binary name, load status, file path, and analysis status
            """
            context_manager: BinAssistMCPBinaryContextManager = ctx.request_context.lifespan_context
            try:
                binary_info = context_manager.get_binary_info(filename)
                return {
                    "name": binary_info.name,
                    "loaded": True,
                    "file_path": str(binary_info.file_path) if binary_info.file_path else None,
                    "analysis_complete": binary_info.analysis_complete,
                    "load_time": binary_info.load_time
                }
            except KeyError as e:
                return {
                    "name": filename,
                    "loaded": False,
                    "error": str(e)
                }

        # Analysis tools
        @mcp.tool(annotations=MODIFY_ANNOTATIONS)
        def rename_symbol(filename: str, address_or_name: str, new_name: str, ctx: Context) -> str:
            """Rename a function or data variable

            Args:
                filename: Name of the binary file
                address_or_name: Address (hex string) or name of the symbol
                new_name: New name for the symbol

            Returns:
                Success message string
            """
            context_manager: BinAssistMCPBinaryContextManager = ctx.request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            tools = BinAssistMCPTools(binary_view)
            return tools.rename_symbol(address_or_name, new_name)

        # Information retrieval tools
        # Note: decompile_function, get_function_pseudo_c, get_function_high_level_il,
        # get_function_medium_level_il, get_disassembly consolidated into get_code()
        @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
        def get_functions(filename: str, ctx: Context) -> list:
            """Get list of all functions in the binary

            Args:
                filename: Name of the binary file

            Returns:
                List of function dictionaries with name, address, size, and metadata
            """
            context_manager: BinAssistMCPBinaryContextManager = ctx.request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            tools = BinAssistMCPTools(binary_view)
            return tools.get_functions()

        @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
        def search_functions_by_name(filename: str, search_term: str, ctx: Context) -> list:
            """Search functions by name substring

            Args:
                filename: Name of the binary file
                search_term: Substring to search for

            Returns:
                List of matching functions
            """
            context_manager: BinAssistMCPBinaryContextManager = ctx.request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            tools = BinAssistMCPTools(binary_view)
            return tools.search_functions_by_name(search_term)

        @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
        def get_imports(filename: str, ctx: Context) -> dict:
            """Get imported symbols grouped by module

            Args:
                filename: Name of the binary file

            Returns:
                Dictionary mapping module names to lists of imported symbols
            """
            context_manager: BinAssistMCPBinaryContextManager = ctx.request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            tools = BinAssistMCPTools(binary_view)
            return tools.get_imports()

        @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
        def get_exports(filename: str, ctx: Context) -> dict:
            """Get exported symbols

            Args:
                filename: Name of the binary file

            Returns:
                List of exported symbols with names, addresses, and types
            """
            context_manager: BinAssistMCPBinaryContextManager = ctx.request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            tools = BinAssistMCPTools(binary_view)
            return tools.get_exports()

        @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
        def get_strings(filename: str, ctx: Context, page_size: int = 100, page_number: int = 1) -> dict:
            """Get strings found in the binary with pagination

            Args:
                filename: Name of the binary file
                page_size: Number of strings per page (default: 100)
                page_number: Page number starting from 1 (default: 1)

            Returns:
                Dictionary with strings list, page_size, page_number, total_count, and total_pages
            """
            context_manager: BinAssistMCPBinaryContextManager = ctx.request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            tools = BinAssistMCPTools(binary_view)
            return tools.get_strings(page_size=page_size, page_number=page_number)

        @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
        def get_segments(filename: str, ctx: Context) -> list:
            """Get memory segments

            Args:
                filename: Name of the binary file

            Returns:
                List of segments with start, end, length, and permissions
            """
            context_manager: BinAssistMCPBinaryContextManager = ctx.request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            tools = BinAssistMCPTools(binary_view)
            return tools.get_segments()

        @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
        def get_sections(filename: str, ctx: Context) -> list:
            """Get binary sections

            Args:
                filename: Name of the binary file

            Returns:
                List of sections with name, start, end, length, and metadata
            """
            context_manager: BinAssistMCPBinaryContextManager = ctx.request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            tools = BinAssistMCPTools(binary_view)
            return tools.get_sections()
            
        # Non-idempotent tool - triggers analysis
        ANALYSIS_ANNOTATIONS = {
            "readOnlyHint": False,
            "idempotentHint": False,
            "openWorldHint": False
        }

        @mcp.tool(annotations=ANALYSIS_ANNOTATIONS)
        def update_analysis_and_wait(filename: str, ctx: Context) -> str:
            """Update binary analysis and wait for completion

            Args:
                filename: Name of the binary file

            Returns:
                Success message string
            """
            context_manager: BinAssistMCPBinaryContextManager = ctx.request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            tools = BinAssistMCPTools(binary_view)
            result = tools.update_analysis_and_wait()
            # Update context manager status
            context_manager.update_analysis_status(filename)
            return result

        # Class and namespace management tools
        @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
        def get_classes(filename: str, ctx: Context) -> list:
            """Get all classes/structs/types in the binary

            Args:
                filename: Name of the binary file

            Returns:
                List of class/struct definitions with members
            """
            context_manager: BinAssistMCPBinaryContextManager = ctx.request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            tools = BinAssistMCPTools(binary_view)
            return tools.get_classes()

        # create_class and add_class_member consolidated into types_tool()

        @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
        def get_namespaces(filename: str, ctx: Context) -> list:
            """Get all namespaces in the binary

            Args:
                filename: Name of the binary file

            Returns:
                List of namespaces with their symbols
            """
            context_manager: BinAssistMCPBinaryContextManager = ctx.request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            tools = BinAssistMCPTools(binary_view)
            return tools.get_namespaces()

        # Advanced data management tools
        @mcp.tool(annotations=MODIFY_ANNOTATIONS)
        def create_data_var(filename: str, address: str, var_type: str, ctx: Context, name: Optional[str] = None) -> str:
            """Create a data variable at the specified address

            Args:
                filename: Name of the binary file
                address: Address in hex format (e.g., '0x401000')
                var_type: Type of the variable (e.g., 'int32_t', 'char*')
                name: Optional name for the variable

            Returns:
                Success message
            """
            context_manager: BinAssistMCPBinaryContextManager = ctx.request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            tools = BinAssistMCPTools(binary_view)
            return tools.create_data_var(address, var_type, name)

        @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
        def get_data_vars(filename: str, ctx: Context) -> list:
            """Get all data variables in the binary

            Args:
                filename: Name of the binary file

            Returns:
                List of data variables with address, type, size, and name
            """
            context_manager: BinAssistMCPBinaryContextManager = ctx.request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            tools = BinAssistMCPTools(binary_view)
            return tools.get_data_vars()

        @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
        def get_data_at(filename: str, address: str, ctx: Context, size: Optional[int] = None) -> dict:
            """Get data at a specific address

            Args:
                filename: Name of the binary file
                address: Address in hex format
                size: Optional size to read (if not specified, uses data var size or default 16)

            Returns:
                Dictionary with data information including hex, raw bytes, and interpreted values
            """
            context_manager: BinAssistMCPBinaryContextManager = ctx.request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            tools = BinAssistMCPTools(binary_view)
            return tools.get_data_at_address(address, size)

        # Comment management tools consolidated into comments_tool()
        # Variable management tools consolidated into variables_tool()
        # Type system tools consolidated into types_tool()

        # Function analysis tools
        # get_call_graph and get_cross_references consolidated into xrefs_tool()

        @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
        def analyze_function(filename: str, function_name_or_address: str, ctx: Context):
            """Perform comprehensive analysis of a function

            Args:
                filename: Name of the binary file
                function_name_or_address: Function name or address

            Returns:
                Comprehensive function analysis including control flow, complexity, and call information
            """
            context_manager: BinAssistMCPBinaryContextManager = ctx.request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            tools = BinAssistMCPTools(binary_view)
            return tools.analyze_function(function_name_or_address)

        @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
        def get_function_signature(filename: str, function_name_or_address: str, ctx: Context) -> dict:
            """Get the native BinAssist byte signature for a function.

            Args:
                filename: Name of the binary file
                function_name_or_address: Function name or address

            Returns:
                Dictionary with function name, address, and byte signature
            """
            context_manager: BinAssistMCPBinaryContextManager = ctx.request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            tools = BinAssistMCPTools(binary_view)
            return tools.get_function_signature(function_name_or_address)

        # Enhanced function listing tools
        @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
        def get_functions_advanced(filename: str, ctx: Context,
                                   name_filter: str = "",
                                   min_size: int = 0,
                                   max_size: int = 0,
                                   has_parameters: bool = False,
                                   sort_by: str = "address",
                                   limit: int = 0):
            """Get functions with advanced filtering and search capabilities

            Args:
                filename: Name of the binary file
                name_filter: Filter by function name (substring match)
                min_size: Minimum function size in bytes
                max_size: Maximum function size in bytes
                has_parameters: Filter by whether function has parameters
                sort_by: Sort by 'address', 'name', 'size', or 'complexity'
                limit: Maximum number of results

            Returns:
                Filtered and sorted list of functions
            """
            context_manager: BinAssistMCPBinaryContextManager = ctx.request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            tools = BinAssistMCPTools(binary_view)
            # Convert empty/zero values back to None for the underlying function
            name_filter_val = name_filter if name_filter else None
            min_size_val = min_size if min_size > 0 else None
            max_size_val = max_size if max_size > 0 else None
            has_parameters_val = has_parameters if has_parameters else None
            limit_val = limit if limit > 0 else None
            return tools.get_functions_advanced(name_filter_val, min_size_val, max_size_val, has_parameters_val, sort_by, limit_val)

        @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
        def search_functions_advanced(filename: str, search_term: str, ctx: Context,
                                      search_in: str = "name",
                                      case_sensitive: bool = False):
            """Advanced function search with multiple search targets

            Args:
                filename: Name of the binary file
                search_term: Term to search for
                search_in: Where to search ('name', 'comment', 'calls', 'variables')
                case_sensitive: Whether search should be case sensitive

            Returns:
                List of matching functions
            """
            context_manager: BinAssistMCPBinaryContextManager = ctx.request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            tools = BinAssistMCPTools(binary_view)
            return tools.search_functions_advanced(search_term, search_in, case_sensitive)

        @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
        def get_function_statistics(filename: str, ctx: Context):
            """Get comprehensive statistics about all functions in the binary

            Args:
                filename: Name of the binary file

            Returns:
                Statistics including size, complexity, parameters, and top functions
            """
            context_manager: BinAssistMCPBinaryContextManager = ctx.request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            tools = BinAssistMCPTools(binary_view)
            return tools.get_function_statistics()

        # Current context tools
        @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
        def get_current_address(filename: str, ctx: Context):
            """Get the current address/offset in the binary view

            Args:
                filename: Name of the binary file

            Returns:
                Dictionary containing current address information with context
            """
            context_manager: BinAssistMCPBinaryContextManager = ctx.request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            tools = BinAssistMCPTools(binary_view)
            return tools.get_current_address()

        @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
        def get_current_function(filename: str, ctx: Context):
            """Get the current function (function containing the current address)

            Args:
                filename: Name of the binary file

            Returns:
                Dictionary containing current function name and address
            """
            context_manager: BinAssistMCPBinaryContextManager = ctx.request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            tools = BinAssistMCPTools(binary_view)
            return tools.get_current_function()

        # ==================== CONSOLIDATED TOOLS ====================
        # These unified tools reduce tool count while maintaining functionality

        @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
        def get_code(filename: str, function_name_or_address: str, ctx: Context,
                    format: str = "decompile") -> dict:
            """Get function code in specified format (unified tool).

            Consolidates: decompile_function, get_function_pseudo_c, get_function_high_level_il,
            get_function_medium_level_il, get_disassembly, get_function_low_level_il

            Args:
                filename: Name of the binary file
                function_name_or_address: Function identifier
                format: Output format - 'decompile', 'hlil', 'mlil', 'llil', 'disasm', 'pseudo_c'

            Returns:
                Dictionary with function info and code
            """
            context_manager: BinAssistMCPBinaryContextManager = ctx.request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            tools = BinAssistMCPTools(binary_view)
            return tools.get_code(function_name_or_address, format)

        @mcp.tool(annotations=MODIFY_ANNOTATIONS)
        def comments(filename: str, action: str, ctx: Context,
                         address: str = "", text: str = "",
                         function_name_or_address: str = ""):
            """Unified comment management (set/get/list/remove comments).

            Args:
                filename: Name of the binary file
                action: 'get', 'set', 'list', 'remove', or 'set_function'
                address: Address for get/set/remove
                text: Comment text for set/set_function
                function_name_or_address: Function for set_function

            Returns:
                Varies by action
            """
            context_manager: BinAssistMCPBinaryContextManager = ctx.request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            tools = BinAssistMCPTools(binary_view)
            return tools.comments(action, address, text, function_name_or_address)

        @mcp.tool(annotations=MODIFY_ANNOTATIONS)
        def variables(filename: str, action: str, function_name_or_address: str = "", ctx: Context = None,
                          var_name: str = "", var_type: str = "",
                          new_name: str = "", storage: str = "auto",
                          scope: str = "auto", address_or_name: str = ""):
            """Unified variable management (list/create/rename/set_type) for local and global variables.

            Args:
                filename: Name of the binary file
                action: 'list', 'create', 'rename', or 'set_type'
                function_name_or_address: Function identifier for local variable operations
                var_name: Variable name, or global symbol name fallback for global rename
                var_type: Variable type
                new_name: New name for rename
                storage: Storage type for create
                scope: Rename scope ('auto', 'local', 'global')
                address_or_name: Global/data symbol address or name for global rename

            Returns:
                List or success message
            """
            context_manager: BinAssistMCPBinaryContextManager = ctx.request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            tools = BinAssistMCPTools(binary_view)
            return tools.variables_unified(
                action,
                function_name_or_address,
                var_name,
                var_type,
                new_name,
                storage,
                scope,
                address_or_name,
            )

        @mcp.tool(annotations=MODIFY_ANNOTATIONS)
        def types(filename: str, action: str, ctx: Context,
                      name: str = "", definition: str = "", size: int = 0,
                      members: dict = None, base_type: str = "", class_name: str = "",
                      member_name: str = "", member_type: str = "", offset: int = 0):
            """Unified type management (list/info/create/create_class/create_enum/create_typedef/add_member).

            Args:
                filename: Name of the binary file
                action: 'list', 'info', 'create', 'create_class', 'create_enum', 'create_typedef', 'add_member'
                name: Type/class/enum name
                definition: C-like type definition
                size: Size in bytes for create_class
                members: Dict of enum members
                base_type: Base type for typedef
                class_name: Class for add_member
                member_name: Member name
                member_type: Member type
                offset: Member offset

            Returns:
                Varies by action
            """
            context_manager: BinAssistMCPBinaryContextManager = ctx.request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            tools = BinAssistMCPTools(binary_view)
            return tools.types_unified(action, name, "", definition, size, members, base_type,
                                       class_name, member_name, member_type, offset)

        @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
        def xrefs(filename: str, address_or_name: str, ctx: Context,
                      direction: str = "both", include_calls: bool = True):
            """Unified cross-reference tool (xrefs + call graph).

            Args:
                filename: Name of the binary file
                address_or_name: Address or symbol name
                direction: 'to', 'from', or 'both'
                include_calls: Include call graph info

            Returns:
                Cross-reference information
            """
            context_manager: BinAssistMCPBinaryContextManager = ctx.request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            tools = BinAssistMCPTools(binary_view)
            return tools.xrefs(address_or_name, direction, include_calls)

        # ==================== NEW TOOLS (Phase 7) ====================

        @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
        def get_function_low_level_il(filename: str, address_or_name: str, ctx: Context) -> str:
            """Get Low Level IL for a function.

            Args:
                filename: Name of the binary file
                address_or_name: Function name or address

            Returns:
                LLIL as string
            """
            context_manager: BinAssistMCPBinaryContextManager = ctx.request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            tools = BinAssistMCPTools(binary_view)
            return tools.get_function_low_level_il(address_or_name)

        @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
        def search_strings(filename: str, pattern: str, ctx: Context,
                          case_sensitive: bool = False,
                          page_size: int = 100, page_number: int = 1) -> dict:
            """Search for strings matching a pattern with pagination.

            Args:
                filename: Name of the binary file
                pattern: Search pattern (substring match)
                case_sensitive: Case-sensitive matching
                page_size: Number of results per page (default: 100)
                page_number: Page number starting from 1 (default: 1)

            Returns:
                Dictionary with strings list, page_size, page_number, total_count, and total_pages
            """
            context_manager: BinAssistMCPBinaryContextManager = ctx.request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            tools = BinAssistMCPTools(binary_view)
            return tools.search_strings(pattern, case_sensitive,
                                        page_size=page_size, page_number=page_number)

        @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
        def search_bytes(filename: str, pattern: str, ctx: Context,
                        start_address: str = "", max_results: int = 100) -> list:
            """Search for byte patterns in the binary.

            Args:
                filename: Name of the binary file
                pattern: Hex pattern (e.g., '90 90 90')
                start_address: Optional start address
                max_results: Maximum results

            Returns:
                List of matches
            """
            context_manager: BinAssistMCPBinaryContextManager = ctx.request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            tools = BinAssistMCPTools(binary_view)
            return tools.search_bytes(pattern, start_address, max_results)

        @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
        def get_basic_blocks(filename: str, function_name_or_address: str, ctx: Context) -> list:
            """Get basic blocks for a function (CFG).

            Args:
                filename: Name of the binary file
                function_name_or_address: Function identifier

            Returns:
                List of basic blocks
            """
            context_manager: BinAssistMCPBinaryContextManager = ctx.request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            tools = BinAssistMCPTools(binary_view)
            return tools.get_basic_blocks(function_name_or_address)

        @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
        def get_function_stack_layout(filename: str, function_name_or_address: str, ctx: Context) -> dict:
            """Get stack frame layout for a function.

            Args:
                filename: Name of the binary file
                function_name_or_address: Function identifier

            Returns:
                Stack layout information
            """
            context_manager: BinAssistMCPBinaryContextManager = ctx.request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            tools = BinAssistMCPTools(binary_view)
            return tools.get_function_stack_layout(function_name_or_address)

        @mcp.tool(annotations=MODIFY_ANNOTATIONS)
        def batch_rename(filename: str, renames: list, ctx: Context) -> list:
            """Batch rename multiple symbols.

            Args:
                filename: Name of the binary file
                renames: List of {address_or_name, new_name} dicts

            Returns:
                List of results for each rename
            """
            context_manager: BinAssistMCPBinaryContextManager = ctx.request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            tools = BinAssistMCPTools(binary_view)
            return tools.batch_rename(renames)

        # ==================== ENTRY POINTS & BOOKMARKS ====================

        @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
        def get_entry_points(filename: str, ctx: Context) -> list:
            """Get entry points of the binary.

            Args:
                filename: Name of the binary file

            Returns:
                List of entry point info dicts
            """
            context_manager: BinAssistMCPBinaryContextManager = ctx.request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            entry_funcs = []
            if binary_view.entry_point is not None:
                ep = binary_view.entry_point
                func = binary_view.get_function_at(ep)
                entry_funcs.append({
                    "address": hex(ep),
                    "name": func.name if func else "entry",
                    "type": "EntryPoint"
                })
            for func in binary_view.functions:
                sym = binary_view.get_symbol_at(func.start)
                if sym and func.start != (binary_view.entry_point or 0):
                    if sym.binding == bn.SymbolBinding.GlobalBinding:
                        entry_funcs.append({
                            "address": hex(func.start),
                            "name": func.name,
                            "type": "Export"
                        })
            return entry_funcs

        @mcp.tool(annotations=MODIFY_ANNOTATIONS)
        def bookmarks(filename: str, action: str, ctx: Context,
                      address: Optional[str] = None,
                      comment: Optional[str] = None) -> str:
            """Manage bookmarks: list, set, or remove.

            Args:
                filename: Name of the binary file
                action: Operation: 'list', 'set', or 'remove'
                address: Address for set/remove (hex string)
                comment: Comment text for set
            """
            context_manager: BinAssistMCPBinaryContextManager = ctx.request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            tools = BinAssistMCPTools(binary_view)

            if action == "list":
                results = []
                # Use get_all_tags_of_type if a Bookmarks tag type exists
                for tt_name in binary_view.tag_types:
                    tt = binary_view.tag_types[tt_name]
                    tagged = binary_view.get_all_tags_of_type(tt)
                    for addr, tag in tagged:
                        func = binary_view.get_function_at(addr)
                        func_label = f" [{func.name}]" if func else ""
                        results.append(f"{hex(addr)}{func_label} ({tt_name}): {tag.data}")
                return "\n".join(results) if results else "No bookmarks found"

            elif action == "set":
                if not address:
                    return "Error: address required for set"
                addr = tools._resolve_symbol(address)
                if addr is None:
                    return f"Error: cannot resolve '{address}'"
                tt = binary_view.tag_types.get("Bookmarks")
                if tt is None:
                    tt = binary_view.create_tag_type("Bookmarks", "⭐")
                text = comment or "Bookmark"
                tag = binary_view.create_tag(tt, text, True)
                func = binary_view.get_function_at(addr)
                if func:
                    func.add_user_address_tag(addr, tag)
                else:
                    binary_view.add_tag(addr, tag, True)
                return f"Bookmark set at {hex(addr)}: {text}"

            elif action == "remove":
                if not address:
                    return "Error: address required for remove"
                addr = tools._resolve_symbol(address)
                if addr is None:
                    return f"Error: cannot resolve '{address}'"
                removed = 0
                tt = binary_view.tag_types.get("Bookmarks")
                if tt:
                    tags_at = binary_view.get_tags_at(addr)
                    for tag in tags_at:
                        if tag.type == tt:
                            binary_view.remove_user_data_tag(addr, tag)
                            removed += 1
                    func = binary_view.get_function_at(addr)
                    if func:
                        func_tags = func.get_address_tags_at(addr)
                        for tag in func_tags:
                            if tag.type == tt:
                                func.remove_user_address_tag(addr, tag)
                                removed += 1
                return f"Removed {removed} bookmark(s) at {hex(addr)}" if removed else f"No bookmarks at {hex(addr)}"

            return f"Invalid action '{action}'. Use 'list', 'set', or 'remove'"

        # ==================== TASK MANAGEMENT TOOLS ====================

        @mcp.tool(annotations=MODIFY_ANNOTATIONS)
        async def start_task(name: str, tool_name: str, ctx: Context) -> dict:
            """Start an asynchronous background task.

            Args:
                name: Human-readable task name
                tool_name: Name of the tool to run
            """
            task_manager = get_task_manager()
            # Submit a placeholder coroutine; real tool dispatch can be added later
            async def _noop():
                return {"message": f"Task '{name}' completed (tool: {tool_name})"}
            task_id = await task_manager.submit(_noop, name=name)
            return {"task_id": task_id, "status": "pending", "message": f"Task '{name}' submitted"}

        @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
        def get_task_status(task_id: str, ctx: Context) -> dict:
            """Get status of an async task.

            Args:
                task_id: ID of the task to check

            Returns:
                Task status including progress, result, and error info
            """
            task_manager = get_task_manager()
            return task_manager.get_task_status(task_id)

        @mcp.tool(annotations=MODIFY_ANNOTATIONS)
        def cancel_task(task_id: str, ctx: Context) -> dict:
            """Cancel a running async task.

            Args:
                task_id: ID of the task to cancel

            Returns:
                Cancellation result
            """
            task_manager = get_task_manager()
            success = task_manager.cancel_task(task_id)
            return {
                "task_id": task_id,
                "cancelled": success,
                "message": "Task cancellation initiated" if success else "Task not found or already completed"
            }

        @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
        def list_tasks(ctx: Context, status: str = "") -> list:
            """List all async tasks, optionally filtered by status.

            Args:
                status: Optional filter - 'pending', 'running', 'completed', 'failed', 'cancelled'

            Returns:
                List of task information
            """
            task_manager = get_task_manager()
            status_filter = None
            if status:
                try:
                    status_filter = TaskStatus(status)
                except ValueError:
                    pass
            return task_manager.list_tasks(status_filter)

        log.log_info("Registered MCP tools")

    def _register_resources(self, mcp: FastMCP):
        """Register MCP resources"""
        
        @mcp.resource("binassist://{filename}/triage_summary")
        def get_triage_summary_resource(filename: str):
            """Get binary triage summary"""
            context_manager: BinAssistMCPBinaryContextManager = mcp.get_context().request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            tools = BinAssistMCPTools(binary_view)
            return tools.get_triage_summary()
            
        @mcp.resource("binassist://{filename}/functions")
        def get_functions_resource(filename: str):
            """Get functions as a resource"""
            context_manager: BinAssistMCPBinaryContextManager = mcp.get_context().request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            tools = BinAssistMCPTools(binary_view)
            return tools.get_functions()
            
        @mcp.resource("binassist://{filename}/imports")
        def get_imports_resource(filename: str):
            """Get imports as a resource"""
            context_manager: BinAssistMCPBinaryContextManager = mcp.get_context().request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            tools = BinAssistMCPTools(binary_view)
            return tools.get_imports()
            
        @mcp.resource("binassist://{filename}/exports")
        def get_exports_resource(filename: str):
            """Get exports as a resource"""
            context_manager: BinAssistMCPBinaryContextManager = mcp.get_context().request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            tools = BinAssistMCPTools(binary_view)
            return tools.get_exports()
            
        @mcp.resource("binassist://{filename}/strings")
        def get_strings_resource(filename: str):
            """Get strings as a resource (first 100 strings, use get_strings tool for pagination)"""
            context_manager: BinAssistMCPBinaryContextManager = mcp.get_context().request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            tools = BinAssistMCPTools(binary_view)
            return tools.get_strings(page_size=100, page_number=1)

        @mcp.resource("binja://{filename}/info")
        def get_binary_info_resource(filename: str):
            """Get comprehensive binary metadata"""
            from .resources import get_binary_info_resource as get_info
            context_manager: BinAssistMCPBinaryContextManager = mcp.get_context().request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            return get_info(binary_view)

        @mcp.resource("binja://{filename}/segments")
        def get_segments_resource(filename: str):
            """Get memory segments"""
            from .resources import get_segments_resource as get_segs
            context_manager: BinAssistMCPBinaryContextManager = mcp.get_context().request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            return get_segs(binary_view)

        @mcp.resource("binja://{filename}/sections")
        def get_sections_resource(filename: str):
            """Get binary sections"""
            from .resources import get_sections_resource as get_secs
            context_manager: BinAssistMCPBinaryContextManager = mcp.get_context().request_context.lifespan_context
            binary_view = context_manager.get_binary(filename)
            return get_secs(binary_view)

        log.log_info("Registered MCP resources")

    def _register_prompts(self, mcp: FastMCP):
        """Register MCP prompts for guided workflows"""
        from .prompts import PROMPTS, get_prompt

        @mcp.prompt()
        def analyze_function(function_name: str, filename: str) -> str:
            """Comprehensive function analysis workflow.

            Args:
                function_name: Name or address of the function to analyze
                filename: Name of the binary file
            """
            return get_prompt("analyze_function", function_name=function_name, filename=filename)

        @mcp.prompt()
        def identify_vulnerability(function_name: str, filename: str) -> str:
            """Security audit checklist for a function.

            Args:
                function_name: Name or address of the function to audit
                filename: Name of the binary file
            """
            return get_prompt("identify_vulnerability", function_name=function_name, filename=filename)

        @mcp.prompt()
        def document_function(function_name: str, filename: str) -> str:
            """Generate documentation for a function.

            Args:
                function_name: Name or address of the function
                filename: Name of the binary file
            """
            return get_prompt("document_function", function_name=function_name, filename=filename)

        @mcp.prompt()
        def trace_data_flow(address: str, filename: str) -> str:
            """Track data dependencies from an address.

            Args:
                address: Starting address for data flow analysis
                filename: Name of the binary file
            """
            return get_prompt("trace_data_flow", address=address, filename=filename)

        @mcp.prompt()
        def compare_functions(func1: str, func2: str, filename: str) -> str:
            """Diff two functions for similarity/differences.

            Args:
                func1: First function name/address
                func2: Second function name/address
                filename: Name of the binary file
            """
            return get_prompt("compare_functions", func1=func1, func2=func2, filename=filename)

        @mcp.prompt()
        def reverse_engineer_struct(address: str, filename: str) -> str:
            """Recover structure definition from usage patterns.

            Args:
                address: Address where structure is used
                filename: Name of the binary file
            """
            return get_prompt("reverse_engineer_struct", address=address, filename=filename)

        @mcp.prompt()
        def trace_network_data(filename: str) -> str:
            """Trace network send/recv call stacks to analyze protocol structures and find vulnerabilities.

            Covers both POSIX (send/recv/sendto/recvfrom) and Winsock (WSASend/WSARecv) APIs.
            Useful for determining protocol payload data structure format and contents,
            as well as identifying network-related security vulnerabilities.

            Args:
                filename: Name of the binary file
            """
            return get_prompt("trace_network_data", filename=filename)

        log.log_info("Registered MCP prompts")

    def start(self):
        """Start the MCP server with configured transports
        
        Returns:
            True if started successfully, False otherwise
        """
        if self._running:
            log.log_warn("Server is already running")
            return True
            
        try:
            log.log_info("Starting BinAssistMCP server...")
            
            # Also log to Binary Ninja
            try:
                import binaryninja as bn
                log.log_info("BinAssistMCP: Server.start() method called")
            except Exception as bn_log_error:
                log.log_error(f"Failed to log to Binary Ninja: {bn_log_error}")
                import traceback
                log.log_error(f"BN log traceback: {traceback.format_exc()}")
            
            # Validate configuration
            log.log_info("Validating configuration...")
            errors = self.config.validate()
            if errors:
                log.log_error(f"Configuration errors: {errors}")
                try:
                    import binaryninja as bn
                    log.log_error(f"BinAssistMCP configuration errors: {errors}")
                except Exception as bn_log_error:
                    log.log_error(f"Failed to log config errors to Binary Ninja: {bn_log_error}")
                    import traceback
                    log.log_error(f"BN log traceback: {traceback.format_exc()}")
                return False
            log.log_info("Configuration validation passed")
            
            try:
                import binaryninja as bn
                log.log_info("BinAssistMCP: Configuration validation passed")
            except Exception as bn_log_error:
                log.log_error(f"Failed to log validation success to Binary Ninja: {bn_log_error}")
                import traceback
                log.log_error(f"BN log traceback: {traceback.format_exc()}")
                
            # Create MCP server
            log.log_info("Creating MCP server instance...")
            self.mcp_server = self.create_mcp_server()
            log.log_info("MCP server instance created successfully")
            
            # Start SSE transport if enabled
            if self.config.is_transport_enabled(TransportType.SSE):
                log.log_info("SSE transport is enabled, starting SSE server...")
                self._start_sse_server()
            # Start Streamable HTTP transport if enabled
            elif self.config.is_transport_enabled(TransportType.STREAMABLEHTTP):
                log.log_info("Streamable HTTP transport is enabled, starting Streamable HTTP server...")
                self._start_streamablehttp_server()
            else:
                log.log_warn(f"Unknown transport type: {self.config.server.transport}")

            self._running = True
            log.log_info(f"BinAssistMCP server started successfully")
            log.log_info(f"Available transports: {self.config.server.transport.value}")

            if self.config.is_transport_enabled(TransportType.SSE):
                log.log_info(f"SSE endpoint: {self.config.get_sse_url()}")
            elif self.config.is_transport_enabled(TransportType.STREAMABLEHTTP):
                log.log_info(f"Streamable HTTP endpoint: {self.config.get_streamablehttp_url()}")
                
            return True
            
        except Exception as e:
            log.log_error(f"Failed to start server: {e}")
            # Also log to Binary Ninja if available
            try:
                import binaryninja as bn
                log.log_error(f"BinAssistMCP server startup failed: {e}")
                import traceback
                traceback_msg = traceback.format_exc()
                log.log_error(f"Server startup traceback: {traceback_msg}")
            except Exception as bn_log_error:
                log.log_error(f"Failed to log startup error to Binary Ninja: {bn_log_error}")
                import traceback
                log.log_error(f"BN log error traceback: {traceback.format_exc()}")
            self.stop()
            return False
            
    def _start_sse_server(self):
        """Start the SSE server thread with improved error handling"""
        if not self.mcp_server:
            raise RuntimeError("MCP server not created")

        try:
            # Create ASGI app for SSE transport
            log.log_info("Creating SSE ASGI app...")
            log.log_info(f"MCP server type: {type(self.mcp_server)}")

            # FastMCP 2.4.0+ uses sse_app() method
            if hasattr(self.mcp_server, 'sse_app'):
                log.log_info("Using FastMCP sse_app() method")
                asgi_app = self.mcp_server.sse_app()
            elif hasattr(self.mcp_server, 'create_asgi_app'):
                log.log_info("Using create_asgi_app method")
                asgi_app = self.mcp_server.create_asgi_app()
            elif hasattr(self.mcp_server, 'asgi'):
                log.log_info("Using asgi property")
                asgi_app = self.mcp_server.asgi
            elif hasattr(self.mcp_server, '_asgi_app'):
                log.log_info("Using _asgi_app property")
                asgi_app = self.mcp_server._asgi_app
            elif hasattr(self.mcp_server, 'app'):
                log.log_info("Using app property")
                asgi_app = self.mcp_server.app
            elif callable(self.mcp_server):
                log.log_info("MCP server is callable, using it directly as ASGI app")
                asgi_app = self.mcp_server
            else:
                # Let's see what attributes it actually has
                all_attrs = [attr for attr in dir(self.mcp_server) if not attr.startswith('__')]
                log.log_error(f"MCP server attributes: {all_attrs}")

                # Try to find any ASGI-like method
                asgi_methods = [attr for attr in all_attrs if 'asgi' in attr.lower() or 'app' in attr.lower()]
                log.log_error(f"Potential ASGI methods: {asgi_methods}")

                raise RuntimeError("Cannot create ASGI app for SSE transport")

            log.log_info(f"Created SSE ASGI app: {type(asgi_app)}")

            # Wrap the ASGI app with resource management
            wrapped_asgi_app = ResourceManagedASGIApp(asgi_app)
            log.log_info("Wrapped SSE ASGI app with error handling and resource management")

            self.sse_thread = SSEServerThread(wrapped_asgi_app, self.config)
            log.log_info(f"Created SSE server thread for {self.config.server.host}:{self.config.server.port}")
            log.log_info(f"SSE endpoint will be available at: {self.config.get_sse_url()}")

            self.sse_thread.start()
            log.log_info("SSE server thread started")

            # Give the thread a moment to start with better timing
            import time
            time.sleep(0.2)

            if self.sse_thread.is_alive():
                log.log_info("SSE server thread is running and ready for connections")
            else:
                log.log_error("SSE server thread failed to start")
                # Clean up the failed thread reference
                self.sse_thread = None
                raise RuntimeError("SSE server thread failed to start")

        except Exception as e:
            log.log_error(f"Failed to start SSE server: {e}")
            import traceback
            log.log_error(f"SSE startup traceback: {traceback.format_exc()}")
            # Clean up on failure
            if hasattr(self, 'sse_thread') and self.sse_thread:
                try:
                    self.sse_thread.stop()
                    self.sse_thread = None
                except Exception as cleanup_error:
                    log.log_error(f"Error cleaning up failed SSE server: {cleanup_error}")
            raise

    def _start_streamablehttp_server(self):
        """Start the Streamable HTTP server thread"""
        if not self.mcp_server:
            raise RuntimeError("MCP server not created")

        try:
            # Create ASGI app for Streamable HTTP transport
            log.log_info("Creating Streamable HTTP ASGI app...")

            if hasattr(self.mcp_server, 'streamable_http_app'):
                log.log_info("Using streamable_http_app method")
                asgi_app = self.mcp_server.streamable_http_app()
            else:
                raise RuntimeError("FastMCP does not have streamable_http_app method")

            log.log_info(f"Created Streamable HTTP ASGI app: {asgi_app}")

            # Wrap the ASGI app with resource management
            wrapped_asgi_app = ResourceManagedASGIApp(asgi_app)
            log.log_info("Wrapped Streamable HTTP ASGI app with resource management")

            self.streamablehttp_thread = SSEServerThread(wrapped_asgi_app, self.config)
            log.log_info(f"Created Streamable HTTP server thread for {self.config.server.host}:{self.config.server.port}")

            self.streamablehttp_thread.start()
            log.log_info("Streamable HTTP server thread started")

            # Give the thread a moment to start
            import time
            time.sleep(0.2)

            if self.streamablehttp_thread.is_alive():
                log.log_info("Streamable HTTP server thread is running")
            else:
                log.log_error("Streamable HTTP server thread failed to start")
                self.streamablehttp_thread = None
                raise RuntimeError("Streamable HTTP server thread failed to start")

        except Exception as e:
            log.log_error(f"Failed to start Streamable HTTP server: {e}")
            if hasattr(self, 'streamablehttp_thread') and self.streamablehttp_thread:
                try:
                    self.streamablehttp_thread.stop()
                    self.streamablehttp_thread = None
                except Exception as cleanup_error:
                    log.log_error(f"Error cleaning up failed Streamable HTTP server: {cleanup_error}")
            raise

    def stop(self):
        """Stop the MCP server"""
        if not self._running:
            log.log_warn("Server is not running")
            return
            
        log.log_info("Stopping BinAssistMCP server")
        
        try:
            # Stop SSE server with improved cleanup
            if self.sse_thread:
                log.log_info("Stopping SSE server thread")
                try:
                    self.sse_thread.stop()

                    # Wait for thread to finish with proper timeout
                    if self.sse_thread.is_alive():
                        self.sse_thread.join(timeout=10.0)

                    if self.sse_thread.is_alive():
                        log.log_warn("SSE server thread did not stop within 10 second timeout")
                    else:
                        log.log_info("SSE server thread stopped successfully")

                except Exception as stop_error:
                    log.log_error(f"Error stopping SSE server thread: {stop_error}")
                finally:
                    self.sse_thread = None

            # Stop Streamable HTTP server with improved cleanup
            if self.streamablehttp_thread:
                log.log_info("Stopping Streamable HTTP server thread")
                try:
                    self.streamablehttp_thread.stop()

                    # Wait for thread to finish with proper timeout
                    if self.streamablehttp_thread.is_alive():
                        self.streamablehttp_thread.join(timeout=10.0)

                    if self.streamablehttp_thread.is_alive():
                        log.log_warn("Streamable HTTP server thread did not stop within 10 second timeout")
                    else:
                        log.log_info("Streamable HTTP server thread stopped successfully")

                except Exception as stop_error:
                    log.log_error(f"Error stopping Streamable HTTP server thread: {stop_error}")
                finally:
                    self.streamablehttp_thread = None

            # Clear MCP server reference
            if self.mcp_server:
                log.log_info("Clearing MCP server reference")
                self.mcp_server = None
                
        except Exception as e:
            log.log_error(f"Error during server shutdown: {e}")
        finally:
            self._running = False
            log.log_info("BinAssistMCP server stopped")
        
    def is_running(self):
        """Check if the server is running"""
        return self._running
        
        
    def __enter__(self):
        """Context manager entry"""
        self.start()
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.stop()
