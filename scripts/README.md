# Prewritten scripts registry

Scripts in this folder are the **governed, reusable** side of the harness shell
tool. A workflow step can invoke one by name with the right flags, e.g.:

> Run script `hello.ps1 -Name World`

Only files placed here with a known extension can be invoked, and only when the
shell tool is enabled (`SHELL_TOOL_ENABLED=1`). Supported interpreters:

| Extension | Runs with |
|-----------|-----------|
| `.ps1`    | `powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File` |
| `.py`     | the active Python interpreter |
| `.cmd` / `.bat` | `cmd /c` |
| `.sh`     | `bash` |

Path traversal is blocked — a script must physically live inside this folder.
Every argument is scanned against the deny-list before it runs.

The two capabilities this pairs with:

1. **Ad-hoc commands** — one-off commands you write fresh each time (`git status`,
   `pytest -q`, `pip install requests`). Allow-listed base command, no pipes or
   redirection, single process.
2. **Prewritten scripts** — the files here, invoked by name with flags.
