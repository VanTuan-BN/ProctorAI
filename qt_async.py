import traceback

from PyQt5.QtCore import QObject, QRunnable, pyqtSignal


class WorkerSignals(QObject):
    result = pyqtSignal(object)
    error = pyqtSignal(object)
    finished = pyqtSignal()


class BackgroundTask(QRunnable):
    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    def run(self):
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as exc:
            self.signals.error.emit(
                {
                    "message": str(exc),
                    "traceback": traceback.format_exc(),
                    "exception": exc,
                }
            )
        else:
            self.signals.result.emit(result)
        finally:
            self.signals.finished.emit()