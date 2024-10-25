from prompt_toolkit.styles import Style
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit import print_formatted_text
from enum import Enum

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

# Define the styles based on ANSI codes
style = Style.from_dict({
    # Basic colors
    'red': 'fg:#110000',
    'green': 'fg:#003300',
    'blue': 'fg:#0000ff',
    'yellow': 'fg:#ffff00',
    'magenta': 'fg:#ff00ff',
    'cyan': 'fg:#00ffff',
    'gray': 'fg:#808080',
    
    # Bright versions
    'bright-red': 'fg:#ff5555',
    'bright-green': 'fg:#00ff00',
    'bright-blue': 'fg:#5555ff',
    
    # Formatting
    'bold': 'bold',
    'italic': 'italic',
    'underline': 'underline',
    'reverse': 'reverse',
    
    # Combinations
    'error': 'bg:#ff0000 fg:#ffffff bold',
    'warning': 'bg:#ffff00 fg:#000000 bold',
    'info': 'bg:#0000ff fg:#ffffff italic underline',
    
    # Special
    'selected': 'bg:#ffffff #000000 reverse',
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