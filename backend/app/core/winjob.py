"""Processus enfants liés à la vie du parent (Windows, « Job Object » KILL_ON_JOB_CLOSE).

Si le worker est tué (fenêtre fermée, arrêt forcé, plantage), Windows arrête aussitôt son llama-server : la VRAM
n'est jamais retenue par un serveur orphelin. Sans effet hors Windows ou si l'appel échoue (le nettoyage par
fichier PID de gpu.kill_orphan reste en place).
"""
from __future__ import annotations

import logging
import os
import subprocess

log = logging.getLogger("ocr.winjob")
_job = None


def _create_job():
    import ctypes
    from ctypes import wintypes

    class IO_COUNTERS(ctypes.Structure):  # noqa: N801
        _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount",
                                                      "OtherOperationCount", "ReadTransferCount",
                                                      "WriteTransferCount", "OtherTransferCount")]

    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class EXTENDED(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    job = k32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW")
    info = EXTENDED()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):  # ExtendedLimitInformation
        raise OSError(ctypes.get_last_error(), "SetInformationJobObject")
    return k32, job


def kill_with_parent(proc: subprocess.Popen) -> bool:
    """Rattache `proc` au job du processus courant : il mourra avec lui. True si c'est fait."""
    global _job
    if os.name != "nt":
        return False
    try:
        if _job is None:
            _job = _create_job()
        k32, job = _job
        if not k32.AssignProcessToJobObject(job, int(proc._handle)):  # noqa: SLF001
            return False
        return True
    except Exception as e:  # noqa: BLE001
        log.debug("job Windows indisponible (%s)", type(e).__name__)
        return False
