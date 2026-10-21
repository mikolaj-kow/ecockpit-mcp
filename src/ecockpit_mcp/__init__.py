# -*- coding: utf-8 -*-
"""ecockpit-mcp: MCP server exposing e!COCKPIT PLC automation to LLM editors.

Package layout (all three scripts must stay siblings in the installed
package - the server and the command relay locate the executor next to
themselves, and all three read ONE .env searched upward from their own
directory):

    server.py     host-side MCP server (this is what editors connect to)
    relay.py      command relay, armed inside e!COCKPIT's ScriptEngine;
                  polls the command file, exec()s the executor per command
    executor.py   command implementations (drives the open project)
"""

__version__ = "0.1.0"
