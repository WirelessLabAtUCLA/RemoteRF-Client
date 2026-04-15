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

_DIRECT_STYLES = {
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


def _style_string(styles) -> str:
    parts = []
    for style_name in styles:
        direct_style = _DIRECT_STYLES.get(style_name, '')
        if not direct_style:
            continue
        parts.append(direct_style)
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
