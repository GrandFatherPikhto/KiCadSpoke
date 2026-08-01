# gui/single_instance.py
"""
SingleInstanceGuard — Qt-idiomatic (QLocalServer/QLocalSocket, not a lock
file or platform mutex) guard against launching a second kicadstamp_gui.py
while one is already running. Works offscreen and cross-platform (this
project's user runs both Windows and Linux) since it's IPC, not rendering.

Usage (see kicadstamp_gui.py): construct after QApplication, call
try_acquire() immediately — if it returns False, another instance already
owns the name (already pinged to raise itself) and this process should
exit without building a MainWindow. Otherwise this instance now owns the
name and should connect activation_requested to bring its own window to
front, and app.aboutToQuit to release().
"""
import logging

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT_MS = 200


class SingleInstanceGuard(QObject):
    activation_requested = pyqtSignal()

    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        self._name = name
        self._server: QLocalServer = None

    def try_acquire(self) -> bool:
        socket = QLocalSocket(self)
        socket.connectToServer(self._name)
        if socket.waitForConnected(_CONNECT_TIMEOUT_MS):
            socket.write(b"activate")
            socket.waitForBytesWritten(_CONNECT_TIMEOUT_MS)
            socket.disconnectFromServer()
            return False
        socket.abort()

        # A stale server name can be left behind by an unclean shutdown
        # (mainly a Linux Unix-socket-file concern; harmless no-op on
        # Windows/when nothing was actually stale) — clear it defensively
        # before listening so a crashed prior instance doesn't permanently
        # block every future launch.
        QLocalServer.removeServer(self._name)
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_new_connection)
        if not self._server.listen(self._name):
            logger.warning("SingleInstanceGuard: failed to listen on %r: %s",
                            self._name, self._server.errorString())
            return True  # can't guard, but don't block this launch over it
        return True

    def _on_new_connection(self) -> None:
        conn = self._server.nextPendingConnection()
        if conn is None:
            return
        conn.readyRead.connect(self._on_ready_read)

    def _on_ready_read(self) -> None:
        """Drain the ping bytes, then surface the activation request.

        Connected to each accepted socket's readyRead; sender() is the
        socket that wrote, so one named slot serves every inbound
        connection (no tuple-lambda trick needed).
        """
        socket = self.sender()
        if socket is None:
            return
        socket.readAll()
        self.activation_requested.emit()

    def release(self) -> None:
        if self._server is not None:
            self._server.close()
            QLocalServer.removeServer(self._name)
            self._server = None
