"""
Liga e desliga o Ollama junto com o programa.

Comportamento que eu queria:
  - o Ollama só sobe quando o provedor "ollama" é escolhido (se escolher
    OpenRouter, agy etc., ele nem é tocado);
  - se fui EU que liguei, ele desliga quando o programa fecha (Ctrl+C no
    terminal, fechar a janela, até se o Python travar);
  - se ele já estava rodando antes (ex.: Ollama instalado que abre com o
    Windows), eu uso e não desligo, porque não fui eu que abri.

Pra garantir o "desliga quando fechar" no Windows usei um Job Object: é um
recurso do próprio Windows que agrupa processos e mata todos quando o dono
(o nosso Python) morre, mesmo se ele morrer sem conseguir rodar o atexit.
O "ollama serve" ainda abre processos filhos (os runners do modelo), e eles
entram no mesmo grupo. Fora do Windows fica só o atexit.
"""

import atexit
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

from . import config

_processo: subprocess.Popen | None = None
_job = None  # handle do Job Object (precisa ficar vivo enquanto o programa roda)


def _url_raiz() -> str:
    # 127.0.0.1 em vez de localhost: no Windows o localhost tenta IPv6 antes e demora
    return config.OLLAMA_BASE_URL.rstrip("/").removesuffix("/v1").replace("localhost", "127.0.0.1")


def rodando(timeout: float = 1.5) -> bool:
    try:
        return httpx.get(f"{_url_raiz()}/api/version", timeout=timeout).status_code == 200
    except httpx.HTTPError:
        return False


def executavel() -> str | None:
    """Ollama instalado (PATH ou pasta padrão) ou a versão portátil ao lado do projeto."""
    candidatos = [
        shutil.which("ollama"),
        str(Path(os.getenv("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"),
        str(config.RAIZ.parent / "ollama-portable" / "ollama.exe"),
    ]
    return next((c for c in candidatos if c and Path(c).is_file()), None)


def iniciado_por_mim() -> bool:
    return _processo is not None and _processo.poll() is None


def _prender_no_job(proc: subprocess.Popen):
    """Windows: amarra o processo ao nosso; se o Python morrer, o Ollama morre junto."""
    global _job
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class BASIC_LIMIT(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class EXTENDED_LIMIT(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC_LIMIT), ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.OpenProcess.restype = wintypes.HANDLE
    job = kernel32.CreateJobObjectW(None, None)
    info = EXTENDED_LIMIT()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))  # 9 = ExtendedLimitInformation
    handle = kernel32.OpenProcess(0x0001 | 0x0100, False, proc.pid)  # PROCESS_TERMINATE | PROCESS_SET_QUOTA
    if handle and kernel32.AssignProcessToJobObject(job, handle):
        _job = job
    if handle:
        kernel32.CloseHandle(handle)


def garantir(espera_max: int = 90) -> tuple[bool, str]:
    """Liga o Ollama se ainda não estiver rodando. Retorna (ok, mensagem)."""
    global _processo
    if rodando():
        return True, "Ollama já estava rodando." if not iniciado_por_mim() else "Ollama rodando."
    exe = executavel()
    if not exe:
        return False, "Ollama não encontrado. Instale em https://ollama.com/download (ou use a pasta ollama-portable)."

    ambiente = dict(os.environ)
    if "ollama-portable" in exe:
        ambiente["OLLAMA_MODELS"] = str(Path(exe).parent / "models")  # a portátil guarda os modelos ao lado
    ambiente.setdefault("OLLAMA_CONTEXT_LENGTH", "8192")  # prompt do agente + resultado passam de 4k tokens
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    _processo = subprocess.Popen([exe, "serve"], env=ambiente, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, creationflags=flags)
    _prender_no_job(_processo)
    atexit.register(parar)

    inicio = time.time()  # na primeira vez ele leva ~35 s detectando a GPU
    while time.time() - inicio < espera_max:
        if rodando():
            return True, f"Ollama iniciado em {time.time() - inicio:.0f}s (desliga junto com o programa)."
        if _processo.poll() is not None:
            return False, "O Ollama fechou logo depois de abrir (porta 11434 ocupada por outro programa?)."
        time.sleep(1)
    return False, f"O Ollama não respondeu em {espera_max}s."


def parar() -> None:
    """Desliga o Ollama, mas só se fui eu que liguei."""
    global _processo
    if iniciado_por_mim():
        _processo.terminate()
        try:
            _processo.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _processo.kill()
    _processo = None
