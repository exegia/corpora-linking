# Records docs/corpora-convert.gif — the README's hero shot.
#
# Run from the repo root (`make docs`, or `vhs docs/convert.tape`). The setup
# block is hidden: it generates the sample EPUB (docs/sample_epub.py), puts
# the working copy of the CLI on the prompt as `corpora`, and moves to a
# scratch directory so the recording shows nothing but the conversion.
#
# Type and palette match the SVG shots (docs/terminal_svg.py): Google Sans
# Code on #262626, same accent (#F7B500).

Output docs/corpora-convert.gif

Set Shell bash
Set FontFamily "Google Sans Code"
Set FontSize 18
Set Width 1200
Set Height 680
Set Padding 20
Set Framerate 24
Set TypingSpeed 55ms
Set Theme { "name": "corpora", "background": "#262626", "foreground": "#ffffff", "black": "#3a3a3a", "red": "#e95656", "green": "#5bc778", "yellow": "#e7b44a", "blue": "#5b97de", "magenta": "#ba7ed6", "cyan": "#5ebec9", "white": "#ffffff", "brightBlack": "#737373", "brightRed": "#ff7878", "brightGreen": "#7ee096", "brightYellow": "#ffd06e", "brightBlue": "#82b4ff", "brightMagenta": "#d4a0eb", "brightCyan": "#82d7e1", "brightWhite": "#ffffff", "cursor": "#d2a24c", "selection": "#3a3a3a" }

Hide
Type `export PS1="$ "` Enter
Type `export PATH="$PWD/.venv/bin:$PATH"` Enter
Type `corpora() { python -m corpora_cli.cli "$@"; }` Enter
Type `rm -rf /tmp/corpora-docs` Enter
Type `python docs/sample_epub.py /tmp/corpora-docs/field-guide.epub` Enter
Sleep 8s
Type `cd /tmp/corpora-docs && clear` Enter
Sleep 1s
Show

Type `corpora convert field-guide.epub -o field-guide.corpus`
Sleep 700ms
Enter
Sleep 11s

Type `corpora validate field-guide.corpus`
Sleep 500ms
Enter
Sleep 5s

Type `ls`
Sleep 400ms
Enter
Sleep 3s
