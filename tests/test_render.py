import sys

from sphinx_autostub import (Renderer, format_param_mismatches,
                                      generate, partition, toctree,
                                      walk_stubs)

STUB = '''\
"""A small example module."""

from typing import overload

class Button:
    """A clickable push button.

    Args:
        label: The text shown on the button.
    """

    @overload
    def resize(self, extent: "Size") -> "Size": ...

    @overload
    def resize(self, extent: float) -> float:
        """Scale the button uniformly."""

    @property
    def label(self) -> "Label":
        """The caption widget."""

    @label.setter
    def label(self, value: "Label") -> None: ...

    padding: float
    """Spacing around the caption."""

def load_layout(path: "Path") -> "Button":
    """Instantiate a widget tree from a layout file.

    Args:
        file: The layout description.
    """

def _private() -> None: ...
'''


def render(tmp_path):
    path = tmp_path / 'sample.pyi'
    path.write_text(STUB)
    renderer = Renderer()
    intro, entries = renderer.collect(path)
    return renderer, intro, entries


def test_entries_and_exclusion(tmp_path):
    """Collecting a stub yields the module docstring and one entry per public
    top-level name, with underscore names excluded by default."""
    _, intro, entries = render(tmp_path)
    assert intro == ['A small example module.']
    assert list(entries) == ['Button', 'load_layout']


def test_exclude_patterns(tmp_path):
    """The exclude argument also accepts full-match regexes, which replace
    the default underscore rule."""
    path = tmp_path / 'sample.pyi'
    path.write_text(STUB)
    _, entries = Renderer(exclude=[r'load_\w+']).collect(path)
    assert list(entries) == ['Button', '_private']
    # A single pattern may be given as a bare string
    _, entries = Renderer(exclude=r'load_\w+').collect(path)
    assert list(entries) == ['Button', '_private']


def test_class_rendering(tmp_path):
    """Class members render as the matching py: directives. An overload chain
    shares one cross-reference target and a property hides its setter."""
    _, _, entries = render(tmp_path)
    text = '\n'.join(entries['Button'])
    # The documented overload owns the target even though it comes second,
    # so the first one carries :no-index:
    lines = entries['Button']
    i = lines.index('   .. py:method:: Button.resize(extent: Size) -> Size')
    assert lines[i + 1] == '      :no-index:'
    assert '   .. py:method:: Button.resize(extent: float) -> float' in lines
    assert text.count(':no-index:') == 1
    # Property without the setter, carrying the type its signature would drop
    assert '.. py:property:: Button.label' in text
    assert ':type: Label' in text
    assert '.. py:method:: Button.label' not in text
    assert '.. py:attribute:: Button.padding' in text
    assert ':type: float' in text
    assert 'Spacing around the caption.' in text
    # Google-style 'Args:' converted by napoleon
    assert ':param label:' in text


def test_param_mismatch(tmp_path):
    """A parameter documented under 'Args:' but absent from the signature is
    recorded on the renderer, which catches stale docstrings."""
    renderer, _, _ = render(tmp_path)
    assert renderer.param_mismatches == {('load_layout', 'file', 'path')}
    line, = format_param_mismatches(renderer.param_mismatches)
    assert line.startswith('  load_layout') and '(accepts: path)' in line


def test_generate(tmp_path):
    """generate() writes one alphabetical page per module plus an index that
    links them, skipping private stub files."""
    pkg = tmp_path / 'mypkg'
    pkg.mkdir()
    (pkg / '__init__.pyi').write_text(STUB)
    (pkg / 'math.pyi').write_text('def clamp(x: float) -> float: ...\n')
    (pkg / '_impl.pyi').write_text('def hidden() -> None: ...\n')

    out = tmp_path / 'out'
    slugs = generate(pkg, out, 'mypkg')
    assert slugs == ['mypkg', 'mypkg_math']
    assert '.. py:currentmodule:: mypkg.math' in \
        (out / 'mypkg_math.rst').read_text()
    index = (out / 'index.rst').read_text()
    assert '.. toctree::' in index and 'mypkg_math' in index

    # A second run leaves unchanged files untouched, which preserves their
    # mtime and keeps Sphinx's incremental build effective
    before = (out / 'mypkg_math.rst').stat().st_mtime_ns
    generate(pkg, out, 'mypkg')
    assert (out / 'mypkg_math.rst').stat().st_mtime_ns == before


def test_sphinx_extension(tmp_path):
    """Extension mode drives a real HTML build from conf.py settings. A full
    build also exercises pickling of the build environment."""
    from sphinx.application import Sphinx

    pkg = tmp_path / 'extpkg'
    pkg.mkdir()
    (pkg / '__init__.py').write_text('')
    (pkg / '__init__.pyi').write_text(STUB)

    src = tmp_path / 'docs'
    src.mkdir()
    (src / 'conf.py').write_text(
        'import sys\n'
        'sys.path.insert(0, %r)\n'
        "extensions = ['sphinx_autostub']\n"
        "autostub_packages = ['extpkg']\n"
        "autostub_exclude = ['_.*']\n" % str(tmp_path))
    (src / 'index.rst').write_text(
        'Docs\n====\n\n.. toctree::\n\n   api/extpkg/index\n')

    try:
        Sphinx(str(src), str(src), str(tmp_path / 'out'),
               str(tmp_path / 'dt'), 'html').build()
    finally:
        sys.path.remove(str(tmp_path))

    page = (src / 'api' / 'extpkg' / 'extpkg.rst').read_text()
    assert '.. py:class:: Button' in page
    assert '_private' not in page


RICH = '''\
import enum
from typing import TypeAlias

class Color(enum.Enum):
    """A color."""

    RED = 0
    """The warm one."""

    GREEN = 1

class Outer:
    """Holder."""

    class Inner:
        """Nested helper."""

        def get(self) -> int: ...

    @staticmethod
    def make() -> "Outer": ...

    @classmethod
    def default(cls) -> "Outer": ...

    def __eq__(self, arg: object, /) -> bool: ...

class Fancy(Outer):
    """A derived class."""

Alias: TypeAlias = Outer

LIMIT: int
'''


def test_rich_class_features(tmp_path):
    """The long tail of stub constructs: enum members with docstrings, nested
    classes, static and class methods, dunders, bases, aliases, module data."""
    path = tmp_path / 'rich.pyi'
    path.write_text(RICH)
    _, entries = Renderer().collect(path)
    assert list(entries) == ['Color', 'Outer', 'Fancy', 'Alias', 'LIMIT']

    color = '\n'.join(entries['Color'])
    assert '.. py:attribute:: Color.RED' in color
    assert ':value: 0' in color
    assert 'The warm one.' in color
    # The 'enum.Enum' base is noise and stays out
    assert 'Bases:' not in color

    outer = '\n'.join(entries['Outer'])
    assert '   .. py:class:: Outer.Inner' in outer
    assert '.. py:method:: Outer.Inner.get() -> int' in outer
    assert '.. py:staticmethod:: Outer.make() -> Outer' in outer
    assert '.. py:classmethod:: Outer.default() -> Outer' in outer
    # Dunder methods survive the underscore exclusion rule
    assert '.. py:method:: Outer.__eq__(arg: object, /) -> bool' in outer

    fancy = '\n'.join(entries['Fancy'])
    assert 'Bases: :py:obj:`Outer`' in fancy
    assert fancy.index('Bases:') < fancy.index('A derived class.')
    assert 'Type alias for ``Outer``' in '\n'.join(entries['Alias'])
    limit = '\n'.join(entries['LIMIT'])
    assert '.. py:data:: LIMIT' in limit and ':type: int' in limit


def test_docstring_hooks(tmp_path):
    """The style setting picks the napoleon parser, and docstring_filter
    rewrites the raw text before markup conversion."""
    path = tmp_path / 'm.pyi'
    path.write_text('def f(x: int) -> int:\n'
                    '    """Add.\n'
                    '\n'
                    '    Parameters\n'
                    '    ----------\n'
                    '    x : int\n'
                    '        Value.\n'
                    '    """\n')
    _, entries = Renderer(style='numpy').collect(path)
    assert ':param x:' in '\n'.join(entries['f'])
    _, entries = Renderer(
        docstring_filter=lambda d: d.replace('Add', 'Total')).collect(path)
    assert 'Total' in '\n'.join(entries['f'])


def test_partition():
    """partition() groups names by the regex table in table order, sorts each
    group, drops empty sections, and collects leftovers under 'other'."""
    names = ['zeta', 'Button', 'load_layout', 'Checkbox', 'alpha']
    table = {'Application': [r'load_\w+'], 'Widgets': ['Button', r'Check\w+'],
             'Unused': ['nomatch']}
    assert list(partition(names, table, other='Other')) == [
        ('Application', ['load_layout']), ('Widgets', ['Button', 'Checkbox']),
        ('Other', ['alpha', 'zeta'])]


def test_partition_first_match_wins():
    """A name joins only the first section that matches it, and unmatched
    names disappear when no 'other' group is requested."""
    table = {'A': ['x'], 'B': ['x', 'y']}
    assert list(partition(['x', 'y'], table)) == [('A', ['x']), ('B', ['y'])]
    # Without 'other', unmatched names are dropped
    assert list(partition(['z'], table)) == []


def test_partition_warnings():
    """warn() reports the three ways a table can disagree with the names it
    distributes, without changing what partition() yields."""
    table = {'A': ['x', 'y', 'nomatch'], 'B': [r'\w']}
    msgs = []
    assert list(partition(['x', 'y', 'zz'], table, warn=msgs.append)) == [
        ('A', ['x', 'y'])]
    # 'B' overlaps 'A' in both names, which costs one message rather than two
    assert set(msgs) == {
        "'x' belongs to section 'A' and also matches 'B'",
        "section 'A': pattern 'nomatch' matches nothing",
        "'zz' matches no section and is dropped"}
    # An 'other' group is where unmatched names are meant to go
    msgs.clear()
    list(partition(['y', 'zz'], {'B': [r'\w']}, other='Other',
                   warn=msgs.append))
    assert msgs == []


def test_annotation_filter(tmp_path):
    """annotation_filter rewrites every rendered type expression, and nothing
    else: parameter names and default values are out of its reach."""
    path = tmp_path / 'ann.pyi'
    path.write_text('from typing import TypeAlias\n'
                    'class C:\n'
                    '    field: _IntC\n'
                    '    @property\n'
                    '    def size(self) -> _IntC: ...\n'
                    '    def at(self, _IntC: _IntC = _IntC) -> "_IntC": ...\n'
                    'Alias: TypeAlias = _IntC\n'
                    'LIMIT: _IntC\n')
    _, entries = Renderer(
        annotation_filter=lambda a: a.replace('_IntC', 'int')).collect(path)
    text = '\n'.join(entries['C'])
    assert ':type: int' in text and ':type: _IntC' not in text
    assert '.. py:method:: C.at(_IntC: int=_IntC) -> int' in text
    assert 'Type alias for ``int``' in '\n'.join(entries['Alias'])
    assert ':type: int' in '\n'.join(entries['LIMIT'])


def test_toctree():
    """A toctree carries an optional caption, and a hidden one registers its
    pages without listing them."""
    assert toctree('Pages', ['a', 'b'], prefix='/api/') == (
        'Pages\n-----\n\n.. toctree::\n    :maxdepth: 1\n\n'
        '    /api/a\n    /api/b\n\n')
    assert toctree(None, ['a'], hidden=True) == (
        '.. toctree::\n    :hidden:\n\n    a\n\n')


def test_walk_stubs(tmp_path):
    """walk_stubs() maps stub paths to module names, treating __init__.pyi as
    the package itself and skipping private files."""
    pkg = tmp_path / 'p'
    (pkg / 'sub').mkdir(parents=True)
    (pkg / '__init__.pyi').write_text('')
    (pkg / 'sub' / '__init__.pyi').write_text('')
    (pkg / 'sub' / 'x.pyi').write_text('')
    (pkg / '_hidden.pyi').write_text('')
    assert set(dict(walk_stubs(pkg, 'p'))) == {'p', 'p.sub', 'p.sub.x'}


def test_generate_sections(tmp_path):
    """A sections table splits the top-level module into thematic pages, with
    leftovers on 'Other' and the module docstring on the index page."""
    pkg = tmp_path / 'mypkg'
    pkg.mkdir()
    (pkg / '__init__.pyi').write_text(STUB)
    (pkg / 'math.pyi').write_text('def clamp(x: float) -> float: ...\n')

    out = tmp_path / 'out'
    slugs = generate(pkg, out, 'mypkg', sections={'Widgets': [r'Button\w*']})
    assert slugs == ['mypkg_widgets', 'mypkg_other', 'mypkg_math']
    assert '.. py:class:: Button' in (out / 'mypkg_widgets.rst').read_text()
    assert 'load_layout' in (out / 'mypkg_other.rst').read_text()
    # The module docstring moves to the index page
    assert 'A small example module.' in (out / 'index.rst').read_text()


def test_extension_sections(tmp_path):
    """autostub_sections reaches generate() from conf.py, showing that the
    table works as a plain-data Sphinx setting."""
    from sphinx.application import Sphinx

    pkg = tmp_path / 'extpkg2'
    pkg.mkdir()
    (pkg / '__init__.py').write_text('')
    (pkg / '__init__.pyi').write_text(STUB)

    src = tmp_path / 'docs'
    src.mkdir()
    (src / 'conf.py').write_text(
        'import sys\n'
        'sys.path.insert(0, %r)\n'
        "extensions = ['sphinx_autostub']\n"
        "autostub_packages = ['extpkg2']\n"
        'autostub_sections = %r\n' % (str(tmp_path),
                                      {'Widgets': ['Button\\w*']}))
    (src / 'index.rst').write_text(
        'Docs\n====\n\n.. toctree::\n\n   api/extpkg2/index\n')

    try:
        Sphinx(str(src), str(src), str(tmp_path / 'out'),
               str(tmp_path / 'dt'), 'html').build()
    finally:
        sys.path.remove(str(tmp_path))

    api = src / 'api' / 'extpkg2'
    assert '.. py:class:: Button' in (api / 'extpkg2_widgets.rst').read_text()
    assert 'load_layout' in (api / 'extpkg2_other.rst').read_text()
