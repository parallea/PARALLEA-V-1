# Manim LaTeX Setup on Windows

Manim can render `MathTex` and `Tex` scenes only when a LaTeX toolchain is installed. On Windows, MiKTeX is a common choice.

## Install MiKTeX Manually

1. Download the MiKTeX Basic Installer from:
   https://miktex.org/download
2. Run the installer.
3. During setup, enable installing missing packages on-the-fly if prompted.
4. Restart your terminal, VS Code, and backend process after installation.

## Verify Installation

Run:

```powershell
latex --version
dvisvgm --version
```

Both commands should work.

## If Commands Are Not Found

If `latex` or `dvisvgm` is not found, the MiKTeX bin folder is not in `PATH`.

1. Add the MiKTeX bin folder to your Windows `PATH`.
2. Restart the terminal and backend process.
3. Run the verification commands again.

## Optional Command-Line MiKTeX Setup

MiKTeX also supports command-line setup with `miktexsetup`, but this can still require manual download steps or administrator access:

```powershell
miktexsetup --package-set=basic download
miktexsetup install
```

This is optional. The manual installer flow is usually simpler for local development.

## Test Manim After Setup

From the repo root, run:

```powershell
python -m backend.scripts.test_manim_render
```

If LaTeX is installed, the script renders both a text-only scene and a `MathTex` scene. If LaTeX is missing and `MANIM_ALLOW_MATHTEX=auto`, Manim uses text-based fallback behavior instead of crashing.
