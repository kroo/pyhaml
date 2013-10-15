import logging
import haml
from lexer import tokens, HamlParserException
from patch import toks, untokenize
import markdown

doctypes = {
    'xhtml': {
        'strict':
            '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN" '
            '"http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">',
        'transitional':
            '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" '
            '"http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">',
        'basic':
            '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML Basic 1.1//EN" '
            '"http://www.w3.org/TR/xhtml-basic/xhtml-basic11.dtd">',
        'mobile':
            '<!DOCTYPE html PUBLIC "-//WAPFORUM//DTD XHTML Mobile 1.2//EN" '
            '"http://www.openmobilealliance.org/tech/DTD/xhtml-mobile12.dtd">',
        'frameset':
            '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Frameset//EN" '
            '"http://www.w3.org/TR/xhtml1/DTD/xhtml1-frameset.dtd">'
    },
    'html4': {
        'strict':
            '<!DOCTYPE html PUBLIC "-//W3C//DTD HTML 4.01//EN" '
            '"http://www.w3.org/TR/html4/strict.dtd">',
        'frameset':
            '<!DOCTYPE html PUBLIC "-//W3C//DTD HTML 4.01 Frameset//EN" '
            '"http://www.w3.org/TR/html4/frameset.dtd">',
        'transitional':
            '<!DOCTYPE html PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN" '
            '"http://www.w3.org/TR/html4/loose.dtd">'
    },
    'html5': { '': '<!DOCTYPE html>' }
}

doctypes['xhtml'][''] = doctypes['xhtml']['transitional']
doctypes['html4'][''] = doctypes['html4']['transitional']

class HamlCall(object):
    """
Represents a single compiled Python statement.  As the name of this class
 suggests, this statement is usually a call to one of _haml's functions.  It
can also be a line of Python generated by - or =.  Calling str or repr on an
instance of this class will convert it to Python code.
    """

    def __init__(self, **kwargs):
        """
Initializes a HamlCall.  Keyword arguments:

haml: tells which HamlObj created this HamlCall.  This is useful for determining
which HAML line corresponds to a compiled Python line.

func, args: The call will compile down into _haml.func(args[0], args[1], ...).
If func is null it is assumed that this is a script line.

script: Defines the line of Python code (including indentation) this call will
turn into.  If script is defined, func and args must not be defined.
        """

        self.haml = None
        self.args = []
        self.func = None
        self.script = ''
        self.__dict__.update(kwargs)

    def __repr__(self):
        """ Returns a compiled Python string representing this code.
        """
        if (self.func is None):
            return self.script
        else:
            return '%s_haml.%s(%s)' % (
                '\t' * self.depth,
                self.func,
                ','.join(map(str, self.args)),
            )

class HamlObj(object):
    """
An element of the HAMl file (e.g. tag, script, filter).
    """

    def __init__(self, parser, posinfo=None, haml_indent=None):
        """
Initializes a HamlObj.  Argument:

parser: The PLY parser that produced this object.

Keyword argument:

posinfo: A tuple (lineno, text) of the sort produced by get_position_info.

haml_indent: How far this HamlObj is indented in the HAML source code.  If it
is None, it is assumed to be indented at lexer.depth at the time after reading
the object.
        """

        self.parser = parser
        self.src = parser.src
        if posinfo is None:
            posinfo = (self.parser.lineno, "<unknown HAML line>")
        self.posinfo = posinfo
        self.haml_indent = haml_indent

    def begin(self, depth):
        """
Begin an object, which is indented to a certain depth.  This is called when the object is encountered.
        """
        #close things at a higher indentation than this
        while len(self.parser.to_close) > depth:
            self.parser.to_close.pop().end()
        self.parser.last_obj = self
        self.open()
        self.entab()
        self.parser.to_close.append(self)

    def end(self):
        """
End an object.  This is called after all the object's nested objects are done.
        """
        self.detab()
        self.close()

    def call(self, **kwargs):
        """
Add a call to the generated Python code.  Keyword arguments are passed to the
HamlCall constructor.
        """
        last = self.src[-1] if len(self.src) else None
        next = HamlCall(depth=self.parser.depth, haml=self, **kwargs)

        if last != None and last.depth == next.depth:
            #entab and detab cancel each other out
            if next.func == 'detab' and last.func == 'entab':
                return self.src.pop()
            #merge multiple write calls into one
            elif next.func == 'write':
                if next.func == last.func:
                    return last.args.extend(next.args)

        self.src.append(next)

    def push(self, s, **kwargs):
        """
Like call, but also adds an _haml.indent call that will indent the generated
HTML.
        """
        self.indent()
        self.write(s, **kwargs)

    def convert_inline_python(self, s):
        """
Converts a string that can contain inline Python to a Python expression that
returns the string.  This works like repr on strings without inline Python.
Syntax for inline Python:

some text @{python(code)} some more text
=> "some text %s some more text" % (python(code))
A backslash before the * escapes it.
some tex \@{not python some more text
=> "some text \\@{not python some more text"
        """
        fmt = []
        args = []
        def add_literal(lit):
            fmt.append(lit.replace("%", "%%"))

        def read_script():
            tokens = []
            level = 0
            rest = None
            for tok in toks(s):
                _, t, _, (_, col), _ = tok
                if t == '{':
                    level += 1
                elif t == '}':
                    if level == 0:
                        rest = s[col:]
                        break
                    level -= 1
                tokens.append(tok)
            if rest == None:
                #never reached break, so @{ was not closed with }
                self.error("End of line reached when reading inline Python")
            python_code = untokenize(tokens)
            fmt.append("%s")
            args.append("(%s)" % python_code)
            return rest
            
        while True:
            open_index = s.find("@{")
            if open_index == -1:
                add_literal(s)
                break
            backslashes = 0
            for i in xrange(open_index - 1, 0, -1):
                if s[i] == '\\':
                    backslashes += 1
                else:
                    break
            add_literal(s[:open_index - backslashes])
            add_literal('\\' * (backslashes / 2))
            s = s[open_index + 2:]
            if backslashes % 2 == 1:
                add_literal("@{")
            else:
                s = read_script()

        fmt = "".join(fmt)
        if len(args) == 0:
            return repr(fmt.replace('%%', '%'))
        else:
            if len(args) == 1:
                #in case eval(args[0]) is a tuple
                args = args[0] + ","
            else:
                args = ", ".join(args)
            return "%r %% (%s)" % (fmt, args)

    def write(self, s, literal=False, escape=False, preserve_whitespace=False):
        """
Generate Python code that will write string s.  If literal is true, then the
Python code will literally write string s (with inline Python).  If it is false,
s should be a Python expression, and the generated Python code will write
str(s).  If escape is true, then the generated Python code will escape the
string before writing it.
        """
        if literal:
            s = self.convert_inline_python(s)
        else:
            #newline is appended sometimes to handle cases like this:
            #="some string" #a comment
            #if a newline is not appended, the comment will comment out the 
            #close parentheses causing confusing syntax errors.
            #Sometimes this will append a newline to a line of code that doesn't
            #contain a comment, but this does not matter much.
            if "#" in s:
                s = s + "\n"
            s = "unicode(%s)" % s
        if escape:
            s = '_haml.escape(%s)' % s
        if preserve_whitespace:
            s = '_haml.preserve_whitespace(%s)' % s
        self.call(func='write', args=[s])

    def script(self, s):
        """
Generate Python code that will run the Python line s.  s should not contain
indentation as this will be added automatically.
        """
        pre = '\t' * self.parser.depth
        self.call(script=pre + s)

    def attrs(self, id, klass, attrs):
        """
Generate Python code that will write HTML attributes.  Arguments:

id: the value of the id attribute
klass: the value of the class attribute
attrs: the other attributes as a string representation of a Python dictionary
expression (e.g. '{"href": "http://www.getaround.com"}')
        """
        if attrs != '{}' or klass or id:
            args = [repr(id), repr(klass), attrs]
            self.call(func='attrs', args=args)

    def enblock(self):
        """
Causes code internal to this object to be more indented in the generated Python
code.
        """
        self.parser.depth += 1

    def deblock(self):
        """
Called after all code internal to this object is done, to reset indentation to 
its original level before the enblock call.
        """
        self.parser.depth -= 1

    def indent(self):
        """
Generates Python code that will call _haml.indent, which causes the next line of
generated HTML to be indented appropriately.
        """
        self.call(func='indent',
            args=[not self.parser.preserve])

    def trim(self):
        """
Generates Python code that will call _haml.trim, which causes the next indent
call to do nothing.
        """
        self.call(func='trim')

    def entab(self):
        """
Generates Python code that will call _haml.entab, which causes future HTML to be
indented more.
        """
        self.call(func='entab')

    def detab(self):
        """
Generates Python code that will call _haml.detab, which causes future HTML to be
indented less.
        """
        self.call(func='detab')

    def open(self):
        """
Called when the object is opened (when begin is called).
        """
        pass

    def close(self):
        """
Called when the object is closed (when end is called).
        """
        pass

    def no_nesting(self):
        """
For objects that do not permit nesting, this should be called in close() to
ensure that no objects were nested in this one.
        """
        if not self.parser.last_obj is self:
            self.error('illegal nesting')

    def error(self, msg):
        """
Raises a HamlParserException.  The exception will contain the line number and
HAML code of this object for better error reporting.
        """
        raise HamlParserException, (self.posinfo[0], self.posinfo[1], msg)

class Filter(HamlObj):

    def __init__(self, parser, **kwargs):
        HamlObj.__init__(self, parser, **kwargs)
        self.lines = []

    def open(self):
        for l in self.lines:
            self.push(l, literal=True)

class CData(HamlObj):

    def open(self):
        self.push('//<![CDATA[', literal=True)

    def close(self):
        self.push('//]]>', literal=True)

class JavascriptFilter(Filter):

    def open(self):
        depth = len(self.parser.to_close)
        Tag(self.parser, posinfo=self.posinfo, tagname='script',
            hash=repr({'type':'text/javascript'})).begin(depth + 1)
        if self.parser.op.format == 'xhtml':
            CData(self.parser, self.posinfo).begin(depth + 2)
        for l in self.lines:
            self.push(l, literal=True)

class EscapedFilter(Filter):

    def open(self):
        for l in self.lines:
            self.push(l, literal=True, escape=True)

class MarkdownFilter(Filter):
    
    def open(self):
        code = '\n'.join(self.lines)
        html = markdown.markdown(code)
        for line in html.split('\n'):
            self.push(line, literal=True)

class Content(HamlObj):

    def __init__(self, parser, value, **kwargs):
        HamlObj.__init__(self, parser, **kwargs)
        self.value = value

    def open(self):
        self.push(self.value, literal=True)

    def close(self):
        self.no_nesting()

class Script(HamlObj):

    def __init__(self, parser, type='=', value='', **kwargs):
        HamlObj.__init__(self, parser, **kwargs)
        self.type = type
        self.value = value
        self.escape = False
        self.preserve_whitespace = False
        if self.type == '&=':
            self.escape = True
        elif self.type == '=' and parser.op.escape_html:
            self.escape = True
        elif self.type == '~':
            self.preserve_whitespace = True

    def open(self):
        self.push(self.value, escape=self.escape,
            preserve_whitespace=self.preserve_whitespace)

    def close(self):
        pass

class SilentScript(HamlObj):

    def __init__(self, parser, value='', **kwargs):
        HamlObj.__init__(self, parser, **kwargs)
        self.value = value

    def entab(self):
        pass

    def detab(self):
        pass

    def open(self):
        self.script(self.value)
        self.enblock()

    def close(self):
        self.deblock()

class Doctype(HamlObj):

    def __init__(self, parser, **kwargs):
        HamlObj.__init__(self, parser, **kwargs)
        self.xml = False
        self.type = ''

    def open(self):
        if self.xml:
            s = '<?xml version="1.0" encoding="%s"?>'
            self.push(s % self.type, literal=True)
        else:
            s = doctypes[self.parser.op.format][self.type]
            self.push(s, literal=True)

    def close(self):
        self.no_nesting()

class Comment(HamlObj):

    def __init__(self, parser, value='', condition='', **kwargs):
        HamlObj.__init__(self, parser, **kwargs)
        self.value = value.strip()
        self.condition = condition.strip()

    def open(self):
        if self.condition:
            s = '<!--[%s]>' % self.condition
        else:
            s = '<!--'
        if self.value:
            s += ' ' + self.value
        self.push(s, literal=True)

    def close(self):
        if self.condition:
            s = '<![endif]-->'
        else:
            s = '-->'
        if self.value:
            self.write(' ' + s, literal=True)
        else:
            self.push(s, literal=True)

class Tag(HamlObj):

    def __init__(self, parser, hash='', id='', klass='', value=None, 
                 tagname='div', inner=False, outer=False, selfclose=False,
                 **kwargs):
        HamlObj.__init__(self, parser, **kwargs)
        self.hash = hash
        self.id = id
        self.klass = klass
        self.value = value
        self.tagname = tagname
        self.inner = inner
        self.outer = outer
        self.selfclose = selfclose

    def addclass(self, s):
        self.klass = (self.klass + ' ' + s).strip()

    def auto(self):
        return (not self.value and
            (self.selfclose or self.tagname in self.parser.op.autoclose))

    def preserve(self):
        return self.tagname in self.parser.op.preserve

    def push(self, s, closing=False, **kwargs):
        (inner, outer) = (self.inner or self.preserve(), self.outer)
        if closing:
            (inner, outer) = (outer, inner)
        if outer or closing and self is self.parser.last_obj:
            self.trim()
        self.indent()
        self.write(s, **kwargs)
        if inner or self.parser.preserve:
            self.trim()

    def open(self):
        self.push('<' + self.tagname, literal=True)
        self.attrs(self.id, self.klass, self.hash)

        s = '>'
        if self.auto() and self.parser.op.format == 'xhtml':
            s = '/>'
        self.write(s, literal=True)

        if self.value:
            if self.selfclose:
                self.error('self-closing tags cannot have content')
            elif isinstance(self.value, Script):
                self.write(self.value.value, escape=self.value.escape)
            else:
                self.write(self.value, literal=True)

        if self.preserve():
            self.parser.preserve += 1

    def close(self):
        if self.value or self.selfclose:
            self.no_nesting()

        if self.preserve():
            self.parser.preserve -= 1

        s = ''
        if not self.auto() and not self.value or not self.auto():
            s = '</' + self.tagname + '>'
        self.push(s, closing=True, literal=True)

def get_lines_in_position_range(lexdata, lexstart, lexend):
    """
Given a string, a starting position, and an ending position, this function grabs
enough lines to ensure that both the starting and ending positions are included.
Returns a substring of lexdata containing a number of complete lines, with the
first and last newline characters omitted.
    """
    start_line_pos = lexdata.rfind('\n', 0, lexstart)
    if start_line_pos == -1: start_line_pos = 0
    end_line_pos = lexdata.find('\n', lexend)
    if end_line_pos == -1: end_line_pos = len(lexdata) 
    return lexdata[start_line_pos + 1 : end_line_pos]

def get_position_info(p):
    """Given a parsing context, get the line number and text of the current symbol.  Returned in a tuple (line, text)."""
    line = p.lineno(0)
    (lexstart, lexend) = p.lexspan(0)
    return (line, get_lines_in_position_range(p.lexer.lexdata, lexstart, lexend))

def p_haml_doc(p):
    '''haml :
            | doc
            | doc LF'''
    #this code is reached at the end of parsing, so close all unclosed objects
    while len(p.parser.to_close):
        p.parser.to_close.pop().end()

def p_doc(p):
    '''doc : obj
            | doc obj
            | doc LF obj'''
    pass

def p_obj(p):
    '''obj : element
        | filter
        | content
        | comment
        | condcomment
        | doctype
        | script
        | silentscript'''
    depth = p[1].haml_indent
    if depth == None:
        depth = p.lexer.depth
    p[1].begin(depth)

def p_filter(p):
    '''filter : filter FILTERCONTENT
                | filter FILTERBLANKLINES
                | FILTER'''
    if len(p) == 2:
        types = {
            'plain': Filter,
            'javascript': JavascriptFilter,
            'escaped': EscapedFilter,
            'markdown': MarkdownFilter,
        }
        haml_indent, filter_name = p[1]
        if not filter_name in types:
            raise HamlParserException, (
                p.lineno(0), p.lexpos(0), 'Invalid filter: %s' % filter_name)
        p[0] = types[filter_name](p.parser, posinfo=get_position_info(p),
haml_indent = haml_indent)
    elif len(p) == 3:
        p[0] = p[1]
        if isinstance(p[2], int):
            #count of blank lines
            for i in xrange(p[2]):
                p[0].lines.append('')
        else:
            #content
            p[0].lines.append(p[2])

def p_silentscript(p):
    '''silentscript : SILENTSCRIPT'''
    if p.parser.op.suppress_eval:
        raise HamlParserException, (
            p.lineno(0), p.lexpos(0), 'python evaluation is not allowed')
    p[0] = SilentScript(p.parser, posinfo=get_position_info(p), value=p[1])

def p_script(p):
    '''script : SCRIPT'''
    script_type, script = p[1]
    if p.parser.op.suppress_eval:
        script = '""'
    p[0] = Script(p.parser, posinfo=get_position_info(p), type=script_type,
                  value=script)

def p_content(p):
    '''content : value'''
    p[0] = Content(p.parser, p[1], posinfo=get_position_info(p))

def p_doctype(p):
    '''doctype : DOCTYPE'''
    p[0] = Doctype(p.parser, posinfo=get_position_info(p))

def p_htmltype(p):
    '''doctype : DOCTYPE HTMLTYPE'''
    p[0] = Doctype(p.parser, posinfo=get_position_info(p))
    p[0].type = p[2]

def p_xmltype(p):
    '''doctype : DOCTYPE XMLTYPE'''
    p[0] = Doctype(p.parser, posinfo=get_position_info(p))
    if p[2] == '':
        p[2] = 'utf-8'
    p[0].type = p[2]
    p[0].xml = True

def p_condcomment(p):
    '''condcomment : CONDCOMMENT
                | CONDCOMMENT VALUE'''
    p[0] = Comment(p.parser, posinfo=get_position_info(p), condition=p[1])
    if len(p) == 3:
        p[0].value = p[2]

def p_comment(p):
    '''comment : COMMENT
            | COMMENT VALUE'''
    p[0] = Comment(p.parser, posinfo=get_position_info(p))
    if len(p) == 3:
        p[0].value = p[2]

def p_element(p):
    '''element : tag dict trim selfclose text'''
    p[0] = p[1]
    p[0].hash = p[2]
    p[0].inner = '<' in p[3]
    p[0].outer = '>' in p[3]
    p[0].selfclose = p[4]
    p[0].value = p[5]

def p_selfclose(p):
    '''selfclose :
                | '/' '''
    p[0] = len(p) == 2

def p_trim(p):
    '''trim :
        | TRIM'''
    if len(p) == 1:
        p[0] = ''
    else:
        p[0] = p[1]

def p_text(p):
    '''text :
            | value
            | script'''
    if len(p) == 2:
        p[0] = p[1]

def p_value(p):
    '''value : value VALUE
            | VALUE'''
    if len(p) == 2:
        p[0] = p[1]
    elif len(p) == 3:
        p[0] = '%s %s' % (p[1], p[2])

def p_dict(p):
    '''dict :
            | DICT '''
    if len(p) == 1 or p.parser.op.suppress_eval:
        p[0] = '{}'
    else:
        p[0] = p[1]

def p_tag_tagname(p):
    'tag : TAGNAME'
    p[0] = Tag(p.parser, posinfo=get_position_info(p))
    p[0].tagname = p[1]

def p_tag_id(p):
    'tag : ID'
    p[0] = Tag(p.parser, posinfo=get_position_info(p))
    p[0].id = p[1]

def p_tag_class(p):
    'tag : CLASSNAME'
    p[0] = Tag(p.parser, posinfo=get_position_info(p))
    p[0].addclass(p[1])

def p_tag_tagname_id(p):
    'tag : TAGNAME ID'
    p[0] = Tag(p.parser, posinfo=get_position_info(p))
    p[0].tagname = p[1]
    p[0].id = p[2]

def p_tag_tag_class(p):
    'tag : tag CLASSNAME'
    p[0] = p[1]
    p[0].addclass(p[2])

def p_error(p):
    msg = "syntax error %s [%s] file: %s lineno: %s\n" % (
        p,
        p.value[:80],
        haml.eng.op.filename,
        p.lineno
    )
    if haml.eng.op.fail_fast:
      raise Exception(msg)
    else:
      logging.warning(msg)
