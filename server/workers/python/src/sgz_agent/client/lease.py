"""The worker's database lease is acquired before interrupted-job recovery."""
from pathlib import Path


class DataLease:
    def __init__(self, directory):
        import msvcrt
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.file = (directory / 'worker.lock').open('a+b')
        try:
            if self.file.seek(0, 2) == 0:
                self.file.write(b'0')
                self.file.flush()
            self.file.seek(0)
            msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            self.file.close()
            raise RuntimeError('data_directory_busy') from error

    def close(self):
        import msvcrt
        if not self.file.closed:
            self.file.seek(0)
            msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            self.file.close()
