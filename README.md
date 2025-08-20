# Portable Game Shelf

A Flask-based web application for organizing and launching games from local directories with optional sandboxing support.

## Features

- **Game Library Management**: Automatically scans game directories and creates a visual library
- **Cover Art Detection**: Intelligently selects cover images based on aspect ratio
- **Multiple Launch Options**: Configure different launchers per game (different executables, arguments)
- **Sandboxing Support**: 
  - Windows: Sandboxie Plus integration
  - Cross-platform: WSL-based networking isolation
- **Web Interface**: Clean, Bootstrap-based UI for managing your game collection
- **Portable**: Self-contained with games and settings stored in your chosen directory

## Requirements

- Python 3.11+ (tested with 3.11)

I've only tested it on Windows.

### Optional Dependencies

- **[Sandboxie Plus](https://sandboxie-plus.com/)** - For Windows application sandboxing
- **WSL (Windows Subsystem for Linux)** - For running linux executables

## Installation

1. Clone this repository:
```bash
git clone https://github.com/yourusername/portable-game-shelf.git
cd portable-game-shelf
```

2. Create a virtual environment (recommended):
```bash
python -m venv .venv
.venv\Scripts\activate  # Windows
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

## Usage

### Windows (Recommended)
```bash
start-server.bat
```

### Cross-platform
```bash
python app.py [path/to/games/directory]
```

The application will:
- Create a `Games` directory in the parent folder if none specified
- Start a web server at `http://localhost:5000`
- Scan for games and present them in a web interface

### Game Directory Structure

Organize your games like this:
```
Games/
├── Game1/
│   ├── cover.jpg
│   ├── game.exe
│   └── subfolder/
│       └── launcher.exe
├── Game2/
│   ├── box.png
│   └── bin/
│       └── start.sh
└── .gamecrawlerignore  # Optional: exclude directories
```
Supports categories:
```
Games/
├── Game1/
│   ├── Chapter1/
│   │   ├── cover.jpg
│   │   └── game.exe
│   └── Chapter2/
│       ├── cover.jpg
│       └── game.exe
^ Makes two entries
```

## Sandboxing

### Sandboxie Plus (Windows)
- Install [Sandboxie Plus](https://sandboxie-plus.com/)
- **Recommended**: Disable networking for the DefaultBox in Sandboxie settings
- Games can be launched in isolated sandboxes via the web interface

### WSL Network Isolation
The WSL sandbox creates an Ubuntu based `games` distro with:
- Network access disabled after initial setup
- Audio/video passthrough via WSLg
- Automatic dependency installation
- File synchronization between Windows and Linux

**Note**: This is not a full security sandbox - it only isolates network access.

## Configuration

- **Settings**: Available via the web interface at `/settings`
- **Game Metadata**: Stored as `game.json` in each game directory
- **Global Settings**: Stored as `_gamecrawler.json` in your games root
- **Ignore Patterns**: Use `.gamecrawlerignore` (gitignore-style) to exclude directories

## File Structure

- `app.py` - Main entry point
- `gamecrawler/` - Core application modules
- `sandboxed_wsl.ps1` - PowerShell script for WSL sandbox setup
- `app_monolithic.py` - Legacy single-file version (reference only)
- `tests/` - Test suite

## Development

Run tests:
```bash
python -m tests.smoke_test
```

Example Output:
```powershell
(.venv) PS D:\GameCrawler> python -m tests.smoke_test
[OK] Games built: ['GameA', 'GameB']
[OK] Execs A: ['bin/alt.sh', 'bin/play.exe']
[OK] Execs B: ['v1/chapter1.exe', 'v2/chapter2.exe']
[OK] Cover picked for A: box.png
[OK] .gamecrawlerignore respected (GameB hidden).
[OK] .sh routed to PowerShell wrapper (SANDBOXED_WSL_PS1).
[OK] Launch path composed and dispatched (mocked).
[OK] effective_sandbox honored global default: False
[Info] Sandboxie available on this host?: True
```

The project uses a modular structure with separate modules for routes, models, scanning, and launch logic.

## License

MIT License - see LICENSE file for details.

## Platform Support

- **Windows**: Fully supported and tested
- **macOS/Linux**: Not tested, basic functionality may work

## Contributing

This is a personal project, but issues and pull requests are welcome. Please note the Windows-centric nature of many features.
