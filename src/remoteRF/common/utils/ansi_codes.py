from enum import Enum

from prompt_toolkit import print_formatted_text
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.styles import Style

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

# Keep text styling portable by treating color classes as no-ops.
# This preserves the existing call sites while avoiding OS-specific color output.
style = Style.from_dict({
    # Colors are intentionally blank for consistent black-and-white output.
    'red': '',
    'green': '',
    'blue': '',
    'yellow': '',
    'magenta': '',
    'cyan': '',
    'gray': '',

    # Background colors are also disabled.
    'bg-red': '',
    'bg-green': '',
    'bg-blue': '',

    # Bright variants become plain text.
    'bright-red': '',
    'bright-green': '',
    'bright-blue': '',
    
    # Formatting
    'bold': 'bold',
    'italic': 'italic',
    'underline': 'underline',
    'reverse': 'reverse',
    
    # Combined styles keep emphasis without color.
    'error': 'bold',
    'warning': 'bold',
    'info': 'italic underline',
    
    # Special
    'selected': 'reverse',
    'default':''
})

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
        
        style_class = ' '.join(resolved_styles)
        
        formatted_text.append(('class:' + style_class, message))
    
    # Create FormattedText object from pairs
    text = FormattedText(formatted_text)
    
    print_formatted_text(text, style=style)
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
        
        style_class = ' '.join(resolved_styles)
        styled_parts.append(('class:' + style_class, text))
    
    return FormattedText(styled_parts)
