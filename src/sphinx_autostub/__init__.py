"""Generate a Sphinx API reference from PEP 561 type stubs.

The stubs (``pkg/*.pyi``) are read with :mod:`ast`, making the stub tree the
only input. :class:`Renderer` turns stub declarations into reStructuredText
blocks, one per top-level name, and :func:`generate` assembles them into one
page per module. The module also acts as a Sphinx extension, described under
:func:`setup`.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import re
import textwrap
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, cast

from sphinx.ext.napoleon.docstring import GoogleDocstring, NumpyDocstring

if TYPE_CHECKING:
    from sphinx.application import Sphinx

__version__ = '0.2.0'

__all__ = ['Renderer', 'format_param_mismatches', 'generate', 'partition',
           'toctree', 'walk_stubs', 'write_page', 'write_text',
           'find_stub_dir', 'setup']

_FORWARD_REF = re.compile(r'[A-Za-z_][\w.]*')


def _unquote_forward_refs(node: ast.AST) -> None:
    """Rewrite ``arg: 'Foo'`` as ``arg: Foo``.

    nanobind quotes an annotation naming a class the stub defines further down.
    Sphinx renders the quoted form as a plain string, so quotes enclosing a
    dotted name come off.
    """
    for child in ast.walk(node):
        for field in ('annotation', 'returns'):
            ann = getattr(child, field, None)
            if (isinstance(ann, ast.Constant) and isinstance(ann.value, str) and
                    _FORWARD_REF.fullmatch(ann.value)):
                setattr(child, field, ast.Name(id=ann.value))


def _args(a: ast.arguments) -> list[ast.arg]:
    return a.posonlyargs + a.args + a.kwonlyargs + \
        [x for x in (a.vararg, a.kwarg) if x]


def _render_annotation(node: ast.AST,
                       filt: Callable[[str], str] | None) -> str:
    """Render a type expression, passing it through ``filt`` if given."""
    text = ast.unparse(node)
    return filt(text) if filt else text


def _render_signature(fn: ast.FunctionDef,
                      filt: Callable[[str], str] | None = None) -> str:
    """Reconstruct a Python signature string from a stub function definition.

    This rewrites ``fn`` in place, so render each definition once.
    """
    _unquote_forward_refs(fn)
    a = fn.args
    # Each annotation goes through the filter on its own, which keeps it away
    # from parameter names and default values. The rendered text goes back as
    # a bare 'Name', which 'ast.unparse' emits verbatim.
    for arg in _args(a):
        if arg.annotation is not None:
            arg.annotation = ast.Name(id=_render_annotation(arg.annotation,
                                                            filt))
    # Sphinx leaves 'self' and 'cls' implicit. Dropping the leading argument
    # keeps the rest aligned with 'defaults', which pairs with the trailing
    # ones.
    for group in (a.posonlyargs, a.args):
        if group and group[0].arg in ('self', 'cls'):
            del group[0]
            break
    return '(%s)%s' % (ast.unparse(a),
                       ' -> ' + _render_annotation(fn.returns, filt)
                       if fn.returns is not None else '')


def _decorators(fn: ast.FunctionDef) -> set[str]:
    return {ast.unparse(d).split('.')[-1].split('(')[0]
            for d in fn.decorator_list}


def _canonical_overloads(body: Iterable[ast.stmt]) -> set[int]:
    """Ids of the definitions that should own their cross-reference target.

    An overload chain registers one target and the rest carry ':no-index:',
    which keeps them out of the index. The owner is the first overload with a
    docstring, which the binding may declare anywhere in the chain.
    """
    first: dict[str, ast.FunctionDef] = {}
    documented: dict[str, ast.FunctionDef] = {}
    for node in body:
        if isinstance(node, ast.FunctionDef):
            first.setdefault(node.name, node)
            if ast.get_docstring(node):
                documented.setdefault(node.name, node)
    return {id(documented.get(name, node)) for name, node in first.items()}


def _attr_doc(body: Sequence[ast.stmt], i: int) -> str | None:
    """Docstring of ``body[i]``, written as a bare string after the assignment.

    This is how stubgen renders enum member and attribute docstrings.
    """
    nxt = body[i + 1] if i + 1 < len(body) else None
    if (isinstance(nxt, ast.Expr) and isinstance(nxt.value, ast.Constant) and
            isinstance(nxt.value.value, str)):
        return nxt.value.value
    return None


def _rst_block(lines: Iterable[str], indent: str) -> list[str]:
    return [indent + l if l.strip() else '' for l in lines] + ['']


def _section(title: str) -> list[str]:
    return [title, '-' * len(title), '']


def _default_exclude(name: str) -> bool:
    return name.startswith('_')


class Renderer:
    """Renders stub declarations as reStructuredText blocks.

    Args:
        exclude: Names to leave out of the documentation, given either as a
            predicate over unqualified names or as one or more full-match
            regular expressions. Defaults to excluding names that start with
            an underscore. Dunder methods are always kept.
        docstring_filter: Transformation applied to each raw docstring before
            markup conversion.
        annotation_filter: Transformation applied to each type expression
            before it is rendered, e.g. to resolve a private alias that the
            stubs use to spell out implicit conversions.
        style: Docstring markup convention, one of 'google', 'numpy', or 'rst'
            for docstrings that already are reStructuredText.
    """

    def __init__(self,
                 exclude: str | Iterable[str] | Callable[[str], bool] |
                 None = None,
                 docstring_filter: Callable[[str], str] | None = None,
                 annotation_filter: Callable[[str], str] | None = None,
                 style: str = 'google') -> None:
        if exclude is None:
            self.exclude: Callable[[str], bool] = _default_exclude
        elif callable(exclude):
            self.exclude = cast('Callable[[str], bool]', exclude)
        else:
            patterns = [exclude] if isinstance(exclude, str) else list(exclude)
            self.exclude = lambda name: any(
                re.fullmatch(p, name) for p in patterns)
        self.docstring_filter = docstring_filter
        self.annotation_filter = annotation_filter
        self.style = style
        # Parameters documented under 'Args:' that the stub does not accept,
        # as (qualname, parameter, accepted parameters) tuples. Usually a stale
        # docstring in the binding source.
        self.param_mismatches: set[tuple[str, str, str]] = set()

    def convert_docstring(self, doc: str | None) -> list[str]:
        """Rewrite a raw docstring into reStructuredText lines."""
        if not doc:
            return []
        doc = textwrap.dedent(doc)
        if self.docstring_filter:
            doc = self.docstring_filter(doc)
        if self.style != 'rst':
            cls = GoogleDocstring if self.style == 'google' else NumpyDocstring
            doc = str(cls(doc))
        return doc.splitlines()

    def _check_params(self, fn: ast.FunctionDef, qualname: str,
                      doc: list[str]) -> None:
        accepted = {x.arg for x in _args(fn.args)}
        # '_emit_function' drops 'self' wherever it renders a signature, which
        # leaves a property's intact.
        for name in re.findall(r'^:param (\w+):', '\n'.join(doc), re.M):
            if name not in accepted:
                self.param_mismatches.add(
                    (qualname, name, ', '.join(sorted(accepted - {'self'}))))

    def _emit_function(self, fn: ast.FunctionDef, indent: str, directive: str,
                       qualname: str, seen: set[str],
                       owner: bool = True) -> list[str]:
        """Render one function/method, including its overload chain."""
        deco = _decorators(fn)
        kind = next((d for d in ('property', 'staticmethod', 'classmethod')
                     if d in deco), directive)

        header = '%s.. py:%s:: %s%s' % (
            indent, kind, qualname,
            '' if kind == 'property'
            else _render_signature(fn, self.annotation_filter))
        out = [header]
        if qualname in seen or not owner:
            out.append(indent + '   :no-index:')
        else:
            seen.add(qualname)
        # A property renders no signature, so its type would otherwise be lost.
        if kind == 'property' and fn.returns is not None:
            _unquote_forward_refs(fn)
            out.append('%s   :type: %s' % (indent, _render_annotation(
                fn.returns, self.annotation_filter)))
        out.append('')

        doc = self.convert_docstring(ast.get_docstring(fn))
        self._check_params(fn, qualname, doc)
        return out + _rst_block(doc, indent + '   ')

    def _emit_class(self, cls: ast.ClassDef, seen: set[str], prefix: str = '',
                    depth: int = 0) -> list[str]:
        out: list[str] = []
        # Names are relative to the page's 'py:currentmodule' directive.
        qualname = prefix + cls.name
        ind = '   ' * depth
        if depth == 0:
            # A section per entry, so that the theme can build an in-page toc.
            out += _section(qualname)
        out += ['%s.. py:class:: %s' % (ind, qualname), '']

        # The base classes belong next to the signature, ahead of the prose.
        bases = [b for b in map(ast.unparse, cls.bases)
                 if not b.startswith(('Generic', 'enum.'))]
        if bases:
            out += ['%s   Bases: %s' % (ind, ', '.join(
                ':py:obj:`%s`' % b.split('[')[0] for b in bases)), '']

        out += _rst_block(self.convert_docstring(ast.get_docstring(cls)),
                          ind + '   ')

        def emit_attribute(name: str, option: str,
                           doc: str | None) -> list[str]:
            if self.exclude(name):
                return []
            target = '%s.%s' % (qualname, name)
            # Options must follow the directive immediately. A blank line in
            # between turns them into body fields.
            lines = ['%s   .. py:attribute:: %s' % (ind, target)]
            if target in seen:
                lines.append(ind + '      :no-index:')
            seen.add(target)
            lines += ['%s      %s' % (ind, option), '']
            lines += _rst_block(self.convert_docstring(doc), ind + '      ')
            return lines

        body, owners = cls.body, _canonical_overloads(cls.body)
        for i, node in enumerate(body):
            if isinstance(node, ast.ClassDef):
                if not self.exclude(node.name):
                    out += self._emit_class(node, seen, prefix=qualname + '.',
                                            depth=depth + 1)
            elif isinstance(node, ast.FunctionDef):
                if self.exclude(node.name) and not node.name.startswith('__'):
                    continue
                # The getter already carries a property's documentation.
                if 'setter' in _decorators(node):
                    continue
                out += self._emit_function(node, ind + '   ', 'method',
                                           '%s.%s' % (qualname, node.name),
                                           seen, id(node) in owners)
            elif (isinstance(node, ast.AnnAssign) and
                    isinstance(node.target, ast.Name)):
                out += emit_attribute(
                    node.target.id,
                    ':type: %s' % _render_annotation(node.annotation,
                                                     self.annotation_filter),
                    _attr_doc(body, i))
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        out += emit_attribute(
                            t.id, ':value: %s' % ast.unparse(node.value),
                            _attr_doc(body, i))
        return out

    def collect(self, path: str | os.PathLike[str]
                ) -> tuple[list[str], dict[str, list[str]]]:
        """Parse one stub file into an ordered {name: rst-lines} mapping.

        Returns ``(module_docstring, entries)``.
        """
        tree = ast.parse(Path(path).read_text('utf-8'))
        entries: dict[str, list[str]] = {}
        seen: set[str] = set()
        owners = _canonical_overloads(tree.body)
        module_doc = self.convert_docstring(ast.get_docstring(tree))

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                if not self.exclude(node.name):
                    entries[node.name] = self._emit_class(node, seen)
            elif isinstance(node, ast.FunctionDef):
                if self.exclude(node.name):
                    continue
                if node.name not in entries:
                    entries[node.name] = _section(node.name)
                entries[node.name] += self._emit_function(
                    node, '', 'function', node.name, seen, id(node) in owners)
            elif (isinstance(node, ast.AnnAssign) and
                    isinstance(node.target, ast.Name)):
                name = node.target.id
                if self.exclude(name):
                    continue
                ann = _render_annotation(node.annotation,
                                         self.annotation_filter)
                if 'TypeAlias' in ann:
                    target = _render_annotation(
                        node.value, self.annotation_filter) \
                        if node.value else ann
                    # Not '.. py:type::': a type alias appears in annotations,
                    # which Sphinx resolves with the 'py:class' role. That role
                    # does not match objects registered by 'py:type', so the
                    # references would all break.
                    body = ['.. py:class:: %s' % name, '',
                            '   Type alias for ``%s``.' % target, '']
                else:
                    body = ['.. py:data:: %s' % name, '   :type: %s' % ann, '']
                entries[name] = _section(name) + body
        return module_doc, entries


def format_param_mismatches(
        mismatches: Iterable[tuple[str, str, str]]) -> list[str]:
    """Render :attr:`Renderer.param_mismatches` entries as aligned lines."""
    return ['  %-46s :param %s:  (accepts: %s)' %
            (qualname, name, accepted or '-')
            for qualname, name, accepted in sorted(mismatches)]


def write_text(path: str | os.PathLike[str], text: str) -> None:
    """Write ``text`` to ``path`` unless the file already holds exactly that.

    An unchanged file keeps its mtime so that Sphinx's incremental build does
    not re-read the page. Missing parent directories are created.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_text('utf-8') != text:
        path.write_text(text, 'utf-8')


def write_page(out_dir: str | os.PathLike[str], slug: str, title: str,
               chunks: Iterable[Sequence[str]], module: str,
               intro: Sequence[str] = ()) -> str:
    """Write one .rst page with the given rendered chunks."""
    text = '%s\n%s\n\n' % (title, '=' * len(title))
    if intro:
        text += '\n'.join(intro).strip() + '\n\n'
    # Sets the default search scope, so that docstrings can cross-reference
    # siblings by short name (with 'default_role = "py:obj"' in conf.py).
    text += '.. py:currentmodule:: %s\n\n' % module
    for c in chunks:
        text += '\n'.join(c).rstrip() + '\n\n'
    write_text(os.path.join(out_dir, slug + '.rst'), text)
    return slug


def toctree(title: str | None, slugs: Iterable[str], prefix: str = '',
            hidden: bool = False) -> str:
    """Return a toctree of the given pages, under an optional section title.

    A ``prefix`` starting with '/' makes the entries resolve against the
    documentation root, which leaves the page including this file free to
    live anywhere. A ``hidden`` toctree registers its pages without listing
    them, for pages that exist only so that references to them resolve.
    """
    text = '%s\n%s\n\n' % (title, '-' * len(title)) if title else ''
    text += '.. toctree::\n    %s\n\n' % (
        ':hidden:' if hidden else ':maxdepth: 1')
    return text + ''.join('    %s%s\n' % (prefix, s) for s in slugs) + '\n'


def find_stub_dir(package: str,
                  extra_candidates: Iterable[str | os.PathLike[str]] = ()
                  ) -> str | None:
    """Locate the directory that contains ``package/*.pyi``.

    Prefers an installed package and falls back to the given candidate
    directories, e.g. a local build tree.
    """
    try:
        spec = importlib.util.find_spec(package)
    except Exception:
        # Continue with the explicit candidates alone
        spec = None
    candidates: list[str | os.PathLike[str]] = \
        list(spec.submodule_search_locations or ()) if spec else []
    candidates += list(extra_candidates)
    for loc in candidates:
        if os.path.exists(os.path.join(loc, '__init__.pyi')):
            return os.path.normpath(loc)
    return None


def partition(names: Iterable[str], sections: Mapping[str, Sequence[str]],
              other: str | None = None,
              key: Callable[[str], str] = str.lower,
              warn: Callable[[str], None] | None = None
              ) -> Iterator[tuple[str, list[str]]]:
    """Group names by an ordered ``{title: [regex, ...]}`` table.

    Yields ``(title, matched)`` pairs in table order. A name joins the first
    section with a full-match pattern; each group is sorted by ``key``, and
    empty groups are dropped. With ``other``, names that match nothing form a
    final group of that title.

    ``warn`` receives one message per anomaly in the table, ahead of the first
    group: a pattern that matches nothing, a pair of sections that claim the
    same name, and, unless ``other`` collects them, a name that no section
    claims and that is therefore dropped. All three are advisory, since a table
    may legitimately span several builds of a package, and relying on the
    section order to break a tie is a documented liberty.
    """
    names = list(names)
    owner: dict[str, str] = {}
    overlaps: set[tuple[str, str]] = set()
    for title, patterns in sections.items():
        for name in names:
            if not any(re.fullmatch(p, name) for p in patterns):
                continue
            if name not in owner:
                owner[name] = title
            # One example per pair of sections: a broad trailing section would
            # otherwise report every name ahead of it.
            elif warn and (owner[name], title) not in overlaps:
                overlaps.add((owner[name], title))
                warn('%r belongs to section %r and also matches %r'
                     % (name, owner[name], title))
    if warn:
        for title, patterns in sections.items():
            for p in patterns:
                if not any(re.fullmatch(p, n) for n in names):
                    warn('section %r: pattern %r matches nothing'
                         % (title, p))
        if other is None:
            for name in names:
                if name not in owner:
                    warn('%r matches no section and is dropped' % name)
    for title in sections:
        matched = sorted((n for n, t in owner.items() if t == title), key=key)
        if matched:
            yield title, matched
    rest = sorted((n for n in names if n not in owner), key=key)
    if other is not None and rest:
        yield other, rest


def walk_stubs(stub_dir: str | os.PathLike[str],
               package: str) -> Iterator[tuple[str, str]]:
    """Yield ``(module name, path)`` for every stub below ``stub_dir``."""
    for root, dirs, files in os.walk(stub_dir):
        dirs[:] = [d for d in dirs if not d.startswith(('_', '.'))]
        for fn in sorted(files):
            if not fn.endswith('.pyi') or \
                    (fn.startswith('_') and fn != '__init__.pyi'):
                continue
            path = os.path.join(root, fn)
            rel = os.path.relpath(path, stub_dir)
            module = package + '.' + rel[:-len('.pyi')].replace(os.sep, '.')
            if module.endswith('.__init__'):
                module = module[:-len('.__init__')]
            yield module, path


def generate(stub_dir: str | os.PathLike[str],
             out_dir: str | os.PathLike[str], package: str,
             renderer: Renderer | None = None,
             sections: Mapping[str, Sequence[str]] | None = None,
             warn: Callable[[str], None] | None = None) -> list[str]:
    """Render one page per stub module, plus an ``index.rst`` linking them.

    Modules and their entries are laid out alphabetically. With ``sections``,
    the top-level module is instead split into one page per :func:`partition`
    group, leftovers land on an 'Other' page, and the module docstring moves
    to the index. Returns the page slugs in index order. ``warn`` reports
    anomalies in the sections table, as described for :func:`partition`.
    """
    renderer = renderer or Renderer()
    front: list[str] = []
    modules: list[tuple[str, str]] = []
    index_intro: list[str] = []
    for module, path in walk_stubs(stub_dir, package):
        intro, entries = renderer.collect(path)
        if not entries:
            continue
        if module == package and sections:
            index_intro = intro
            for title, names in partition(entries, sections, other='Other',
                                          warn=warn):
                slug = '%s_%s' % (package.replace('.', '_'),
                                  re.sub(r'\W+', '_', title.lower()))
                front.append(write_page(out_dir, slug, title,
                                        [entries[n] for n in names],
                                        module=package))
        else:
            slug = module.replace('.', '_')
            modules.append((module, slug))
            write_page(out_dir, slug, module,
                       [entries[n] for n in sorted(entries, key=str.lower)],
                       module=module, intro=intro)

    slugs = front + [slug for _, slug in sorted(modules)]
    title = '%s API reference' % package
    text = '%s\n%s\n\n' % (title, '=' * len(title))
    if index_intro:
        text += '\n'.join(index_intro).strip() + '\n\n'
    text += toctree(None, slugs)
    write_text(os.path.join(out_dir, 'index.rst'), text)
    return slugs


# --- Sphinx extension glue -------------------------------------------------


def _builder_inited(app: Sphinx) -> None:
    from sphinx.util import logging
    logger = logging.getLogger(__name__)
    cfg = app.config
    # Sphinx pickles config values into the build environment, so the Renderer
    # is assembled here from plain-data settings.
    renderer = Renderer(exclude=cfg.autostub_exclude or None,
                        style=cfg.autostub_style)
    for package in cfg.autostub_packages:
        stub_dir = find_stub_dir(package)
        if stub_dir is None:
            logger.warning('no type stubs found for %r; skipping its API '
                           'reference', package)
            continue
        out_dir = os.path.join(app.srcdir, cfg.autostub_output,
                               package.replace('.', '_'))
        generate(stub_dir, out_dir, package, renderer=renderer,
                 sections=cfg.autostub_sections or None,
                 warn=lambda msg: logger.warning('%s', msg))

    for qualname, name, accepted in sorted(renderer.param_mismatches):
        logger.warning('%s documents a parameter %r that its stub does not '
                       'accept (accepts: %s)', qualname, name, accepted or '-')


def setup(app: Sphinx) -> dict[str, bool | str]:
    """Sphinx extension entry point.

    Set ``autostub_packages = ['mypkg']`` in conf.py to render the stubs of
    each listed package below ``<srcdir>/<autostub_output>/<package>``. Add
    the resulting ``index`` pages to a toctree by hand. ``autostub_exclude``
    (a list of full-match regexes over unqualified names) and
    ``autostub_style`` adjust the rendering, and ``autostub_sections`` (a
    :func:`partition` table) splits the top-level module into thematic pages.
    All settings are plain data so that they survive Sphinx's environment
    pickling.
    """
    # No rebuild flag: a changed setting reaches Sphinx through the content
    # of the regenerated pages, which invalidates exactly the affected ones.
    app.add_config_value('autostub_packages', [], '')
    app.add_config_value('autostub_output', 'api', '')
    app.add_config_value('autostub_exclude', [], '')
    app.add_config_value('autostub_style', 'google', '')
    app.add_config_value('autostub_sections', {}, '')
    app.connect('builder-inited', _builder_inited)
    return {'version': __version__, 'parallel_read_safe': True}
