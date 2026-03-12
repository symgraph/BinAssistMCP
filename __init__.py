"""
BinAssistMCP: Binary Ninja Plugin Entry Point

This file serves as the main entry point for the Binary Ninja plugin.
Binary Ninja requires this __init__.py file in the root directory to recognize the plugin.
"""

# Fix pywin32 paths on Windows (required for mcp library)
# Binary Ninja doesn't process .pth files, so we add the paths manually
import sys
import os
if sys.platform == 'win32':
    _site_packages = os.path.join(os.environ.get('APPDATA', ''), 'Binary Ninja', 'python310', 'site-packages')
    _win32_paths = [
        os.path.join(_site_packages, 'win32'),
        os.path.join(_site_packages, 'win32', 'lib'),
        os.path.join(_site_packages, 'Pythonwin'),
    ]
    for _p in _win32_paths:
        if _p not in sys.path and os.path.isdir(_p):
            sys.path.insert(0, _p)

import logging

# Configure logging early
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)

try:
    import binaryninja as bn
    
    # Try to import our plugin implementation
    try:
        from .src.binassist_mcp.plugin import BinAssistMCPPlugin
        
        # Initialize the plugin
        logger.info("Loading BinAssistMCP plugin...")
        
        # The plugin will automatically register itself when imported
        plugin_instance = BinAssistMCPPlugin()
        
        # Set global plugin instance and register auto-startup callback
        from .src.binassist_mcp.plugin import set_plugin_instance
        set_plugin_instance(plugin_instance)
        
        # Register for binary analysis completion events (for auto-startup)
        def on_binaryview_analysis_completion(bv):
            """Handle binary analysis completion for auto-startup"""
            try:
                from .src.binassist_mcp.plugin import get_plugin_instance
                plugin = get_plugin_instance()
                if plugin:
                    plugin.handle_auto_startup(bv)
            except Exception as e:
                logger.error(f"Error in auto-startup callback: {e}")
                bn.log_error(f"BinAssistMCP auto-startup error: {e}")
        
        # Register the callback with Binary Ninja
        bn.BinaryViewType.add_binaryview_initial_analysis_completion_event(
            on_binaryview_analysis_completion
        )
        
        logger.info("BinAssistMCP plugin loaded successfully")
        bn.log_info("BinAssistMCP plugin loaded successfully")
        bn.log_info("BinAssistMCP auto-startup enabled - server will start automatically when analysis completes")
        
        # Start the MCP server immediately (even without a binary open).
        # The analysis-completion callback above will add binaries as they
        # are opened later.
        if plugin_instance.config and plugin_instance.config.plugin.auto_startup:
            try:
                logger.info("Starting BinAssistMCP server immediately (no binary required)...")
                plugin_instance._start_server_command(None)
            except Exception as start_err:
                logger.error(f"Failed to auto-start server on plugin load: {start_err}")
                bn.log_error(f"BinAssistMCP: failed to auto-start server: {start_err}")
        
    except ImportError as import_err:
        logger.error(f"Failed to import BinAssistMCP modules: {import_err}")
        bn.log_error(f"BinAssistMCP plugin failed to load - missing dependencies: {import_err}")
        bn.log_info("To fix this, install BinAssistMCP dependencies: pip install anyio hypercorn mcp pydantic pydantic-settings click")
        
    except Exception as plugin_err:
        logger.error(f"Failed to initialize BinAssistMCP plugin: {plugin_err}")
        bn.log_error(f"BinAssistMCP plugin initialization failed: {plugin_err}")
        
except ImportError:
    logger.error("Binary Ninja not available - this should only happen outside of Binary Ninja")
    
except Exception as e:
    logger.error(f"Unexpected error in BinAssistMCP plugin loading: {e}")
    try:
        import binaryninja as bn
        bn.log_error(f"BinAssistMCP plugin unexpected error: {e}")
    except:
        pass