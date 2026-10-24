# -*- coding: utf-8 -*-
"""跨进程文件锁（Windows msvcrt 实现）。
web_app.py 和 auto_dl.py 共享同一把锁，避免同时写 追番列表.json。
锁文件本身是 .list.lock，与数据文件分开。
"""
import msvcrt
import os
import time


class FileLock:
    def __init__(self, lock_path, timeout=10):
        self.lock_path = lock_path
        self.timeout = timeout
        self.fd = None

    def __enter__(self):
        os.makedirs(os.path.dirname(self.lock_path) or ".", exist_ok=True)
        # 'a+' 让文件不存在时创建；不会截断已有内容
        self.fd = open(self.lock_path, "a+")
        deadline = time.time() + self.timeout
        while True:
            try:
                msvcrt.locking(self.fd.fileno(), msvcrt.LK_NBLCK, 1)
                return self
            except OSError:
                if time.time() > deadline:
                    raise TimeoutError(f"文件锁等待超时: {self.lock_path}")
                time.sleep(0.1)

    def __exit__(self, *exc):
        try:
            self.fd.seek(0)
            msvcrt.locking(self.fd.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self.fd.close()


if __name__ == "__main__":
    # 自测：连续两次上锁应能正常退出
    p = ".test.lock"
    with FileLock(p, timeout=2):
        print("第一次上锁 OK")
    with FileLock(p, timeout=2):
        print("第二次上锁 OK")
    os.remove(p)
    print("自测通过")