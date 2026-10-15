"""HTTP servers for the fakes tests stand up."""

import http.server
import socketserver


class LocalServer(http.server.ThreadingHTTPServer):
    """
    A ThreadingHTTPServer without HTTPServer's `getfqdn` of its own address.

    That reverse DNS lookup of 127.0.0.1 sometimes hangs past the test timeout
    on macOS CI runners, and nothing here uses the name it finds.
    """

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = port
