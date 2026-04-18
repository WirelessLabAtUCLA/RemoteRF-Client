import os
import sys
from enum import Enum

from prompt_toolkit import print_formatted_text
from prompt_toolkit.formatted_text import FormattedText

class Sty(Enum):
    # Basic colors
    RED = 'red'
    GREEN = 'green'
    BLUE = 'blue'
    YELLOW = 'yellow'
    MAGENTA = 'magenta'
    CYAN = 'cyan'
    GRAY = 'gray'
    
    # Background colors
    BG_RED = 'bg-red'
    BG_GREEN = 'bg-green'
    BG_BLUE = 'bg-blue'
    
    # Bright versions
    BRIGHT_RED = 'bright-red'
    BRIGHT_GREEN = 'bright-green'
    BRIGHT_BLUE = 'bright-blue'
    
    # Formatting
    BOLD = 'bold'
    ITALIC = 'italic'
    UNDERLINE = 'underline'
    BLINK = 'blink'
    REVERSE = 'reverse'
    
    # Combinations
    ERROR = 'error'
    WARNING = 'warning'
    INFO = 'info'
    
    # Special
    SELECTED = 'selected'
    DEFAULT = 'default'

_COLOR_STYLE_MAP = {
    'red': 'fg:ansired',
    'green': 'fg:ansigreen',
    'blue': 'fg:ansiblue',
    'yellow': 'fg:ansiyellow',
    'magenta': 'fg:ansimagenta',
    'cyan': 'fg:ansicyan',
    'gray': 'fg:ansibrightblack',
    'bg-red': 'bg:ansired',
    'bg-green': 'bg:ansigreen',
    'bg-blue': 'bg:ansiblue',
    'bright-red': 'fg:ansibrightred',
    'bright-green': 'fg:ansibrightgreen',
    'bright-blue': 'fg:ansibrightblue',
}

_MONO_STYLES = {
    'bold': 'bold',
    'italic': 'italic',
    'underline': 'underline',
    'blink': 'blink',
    'reverse': 'reverse',
    'error': 'bold',
    'warning': 'bold',
    'info': 'italic underline',
    'selected': 'reverse',
    'default': '',
}

_COLOR_COMBINATIONS = {
    'error': 'fg:ansired bold',
    'warning': 'fg:ansiyellow bold',
    'info': 'fg:ansiblue italic underline',
    'selected': 'reverse',
}


def _env_flag(name: str) -> str:
    return os.environ.get(name, '').strip().lower()


def _supports_color() -> bool:
    override = _env_flag('REMOTERF_COLOR')
    if override in {'0', 'false', 'no', 'off'}:
        return False
    if override in {'1', 'true', 'yes', 'on'}:
        return True

    if _env_flag('NO_COLOR'):
        return False

    term = _env_flag('TERM')
    if term == 'dumb':
        return False

    try:
        if not sys.stdout.isatty():
            return False
    except Exception:
        return False

    if sys.platform != 'win32':
        return True

    # Only use color in Windows terminals that commonly support ANSI output.
    return any([
        bool(os.environ.get('WT_SESSION')),
        bool(os.environ.get('ANSICON')),
        os.environ.get('ConEmuANSI', '').upper() == 'ON',
        bool(os.environ.get('COLORTERM')),
        bool(os.environ.get('TERM_PROGRAM')),
        term not in {'', 'dumb'},
    ])


def _style_string(styles) -> str:
    parts = []
    use_color = _supports_color()
    for style_name in styles:
        if use_color and style_name in _COLOR_COMBINATIONS:
            style_value = _COLOR_COMBINATIONS[style_name]
        elif use_color and style_name in _COLOR_STYLE_MAP:
            style_value = _COLOR_STYLE_MAP[style_name]
        else:
            style_value = _MONO_STYLES.get(style_name, '')

        if not style_value:
            continue
        parts.append(style_value)
    return ' '.join(parts)

def printf(*args) -> str:
    if len(args) % 2 != 0:
        raise ValueError('Arguments must be in pairs of two.')
    
    # Create formatted text using the defined style
    formatted_text = []
    
    for i in range(0, len(args), 2):
        message = args[i]
        styles = args[i+1]
        
        if not isinstance(styles, tuple):
            styles = (styles,)
            
        resolved_styles = (s.value if isinstance(s, Enum) else s for s in styles)
        
        formatted_text.append((_style_string(resolved_styles), message))
    
    # Create FormattedText object from pairs
    text = FormattedText(formatted_text)
    
    print_formatted_text(text)
    return text

def stylize(*args):
    """
    Create a styled prompt text based on pairs of (text, (Sty, ...), ...).
    """
    if len(args) % 2 != 0:
        raise ValueError("Arguments must be in pairs of (text, style_class).")
    
    styled_parts = []
    for i in range(0, len(args), 2):
        text = args[i]
        styles = args[i + 1]
        
        if not isinstance(styles, tuple):
            styles = (styles,)
            
        resolved_styles = (s.value if isinstance(s, Enum) else s for s in styles)
        
        styled_parts.append((_style_string(resolved_styles), text))
    
    return FormattedText(styled_parts)
