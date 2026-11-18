# HAT Dashboard — Artwork

This focused copy retains only the **HOME** and **Artwork** pages. The Artwork
workflow includes the original global and local crop editors.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

In VS Code, select this project's `.venv\Scripts\python.exe` as the Python
interpreter.

## Artwork workflow

1. On the Artwork page, browse for one or more PDF files.
2. Select **Manual Cut** to open the crop workspace.
3. Use **Global Crop Setup** to set layer or spot-color actions across the
   artwork set. Open an artwork preview to adjust its **Local Crop Setup**.
4. Choose the output folder in the crop workspace and apply the selected cuts.
   The page exports TIFF artwork, with the with-diecut version controlled by
   its checkbox.

The processing engine renders the first page of each PDF. Review detection
warnings and the final images before using them in production.

## Tests

```powershell
python -m unittest discover -s tests
```
