Advanced usage
==============

This project can also be used as a library to implement more advanced
filtering and layout policies. A small driver in ``conf.py`` then replaces
the extension. The following example renders thematic pages for the
top-level module into ``<srcdir>/api/`` at the start of every build:

.. code-block:: python

   import os
   import sphinx_autostub as sas

   # Page layout of the top-level module: one page per section
   SECTIONS = {'Widgets': ['Button', 'Label', r'Checkbox\w*'],
               'Application': [r'load_\w+', r'gui_\w+']}

   # Cut an internal docstring section before rendering
   def drop_notes(doc):
       return doc.split('Implementation notes:')[0]

   def generate_api(app):
       # A callable hook to arbitrarily transform docstrings
       renderer = sas.Renderer(docstring_filter=drop_notes)
       stub_dir = sas.find_stub_dir('mypkg')
       out_dir = os.path.join(app.srcdir, 'api')

       # Parse the installed stubs into one rst block per name
       _, entries = renderer.collect(os.path.join(stub_dir, '__init__.pyi'))

       # Write the section pages; unmatched names land on an 'Other' page
       for title, names in sas.partition(entries, SECTIONS, other='Other'):
           sas.write_page(out_dir, title.lower(), title,
                          [entries[n] for n in names], module='mypkg')

   def setup(app):
       # Regenerate the pages at the start of every Sphinx build
       app.connect('builder-inited', generate_api)

Sphinx only builds pages that a toctree reaches, and the driver above does
not emit one, so the hand-written documentation must list the generated
pages somewhere:

.. code-block:: rst

   .. toctree::

      api/widgets
      api/application
      api/other

A driver can also produce this list itself, and larger projects usually
cover the submodules as well. The following continuation of
``generate_api()`` writes one page per submodule, a toctree fragment for a
hand-written page to ``.. include::``, and a report of stale docstrings:

.. code-block:: python

       # One page per submodule, with entries in alphabetical order
       slugs = []
       for module, path in sas.walk_stubs(stub_dir, 'mypkg'):
           if module == 'mypkg':
               continue  # covered by the section pages above
           intro, entries = renderer.collect(path)
           slugs.append(sas.write_page(out_dir, module.replace('.', '_'),
                                       module,
                                       [entries[n] for n in sorted(entries)],
                                       module=module, intro=intro))

       # The linking structure that the hand-written pages include. The '/'
       # prefix resolves the entries against the documentation root.
       sas.write_text(os.path.join(out_dir, 'submodules.txt'),
                      sas.toctree('Submodules', slugs, prefix='/api/'))

       # Report docstrings that disagree with their stub's signature
       for line in sas.format_param_mismatches(renderer.param_mismatches):
           print(line)

The split of this interface into a class and plain functions follows one
rule: whatever depends on the rendering configuration is a method of
``Renderer``, and everything downstream of the rendered blocks is a plain
function. The individual building blocks:

``Renderer``
------------

.. code-block:: python

   Renderer(exclude: str | list[str] | Callable[[str], bool] | None = None,
            docstring_filter: Callable[[str], str] | None = None,
            annotation_filter: Callable[[str], str] | None = None,
            style: str = 'google')

A ``Renderer`` turns stub declarations into reStructuredText. Its
constructor arguments control what is documented and how docstrings are
interpreted.

- ``exclude``: either one or more full-match regular expressions or a
  predicate that receives an unqualified name (``'Button'``, not
  ``'mypkg.Button'``) and returns ``True`` to leave it undocumented. It
  applies to classes, functions, methods, attributes, and module-level
  data. The default excludes names that start with an underscore. Dunder
  methods such as ``__init__`` are always kept.
- ``docstring_filter``: a function that receives each raw docstring after
  dedenting and returns a replacement, e.g. to drop a section that only
  makes sense in another context. The default keeps docstrings unchanged.
- ``annotation_filter``: a function that receives each type expression as
  text and returns a replacement. It sees parameter and return annotations,
  the type of a property, attribute or module-level variable, and the
  target of a type alias, one at a time, which keeps it away from parameter
  names and default values. Bindings that generate their stubs often name a
  private alias in signatures, such as a union spelling out the types that
  implicitly convert to a parameter; this hook rewrites such a reference as
  the type a reader expects. The default keeps annotations unchanged.
- ``style``: the docstring markup convention. ``'google'`` (the default)
  and ``'numpy'`` run Sphinx's napoleon parser, while ``'rst'`` passes
  docstrings through untouched.

The central method parses one stub file:

.. code-block:: python

   Renderer.collect(path: str) -> tuple[list[str], dict[str, list[str]]]

The first element of the returned pair holds the module docstring as
reStructuredText lines, and the second maps each top-level name to a
self-contained block of such lines, in declaration order. Because the
blocks are independent, a driver can regroup or drop them, or document a
name on a different page than its stub suggests, e.g. to present a
re-export under its public location.

While rendering, the instance records documented parameters that the
signature does not accept in ``param_mismatches``, a set of ``(qualified
name, parameter, accepted parameters)`` tuples. These usually point to
stale docstrings in the binding source; ``format_param_mismatches(...)``
renders the set as a list of aligned lines for reporting.

``partition()``
---------------

.. code-block:: python

   partition(names: Iterable[str], sections: dict[str, list[str]],
             other: str | None = None,
             key: Callable[[str], str] = str.lower,
             warn: Callable[[str], None] | None = None) -> Iterator[tuple[str, list[str]]]

This function distributes names over a sections table and yields
``(title, matched names)`` pairs in table order.

- ``names``: the names to distribute, typically the keys of ``entries``.
- ``sections``: a name joins the first section with a pattern that matches
  it completely.
- ``other``: the title of a final group that collects every unmatched name.
  Without it, unmatched names are dropped.
- ``key``: the sort key applied within each group, case-insensitive by
  default.
- ``warn``: a reporting hook that receives one message per anomaly in the
  table, ahead of the first group: a pattern that matches nothing, a pair of
  sections that claim the same name, and, unless ``other`` collects them, a
  name that no section claims and that is therefore dropped.

Sections that match nothing are omitted.

The three warnings are advisory rather than errors. A table often outlives
the build it was written against, where an optional feature adds or removes
names, and breaking a tie through the section order is a documented liberty.
In a table that draws no warnings, each name lands in its section no matter
how the entries are ordered.

``find_stub_dir()``
-------------------

.. code-block:: python

   find_stub_dir(package: str, extra_candidates: Iterable[str] = ()) -> str | None

This helper locates the directory that contains ``package/*.pyi``. It
prefers an installed package and then checks each directory in
``extra_candidates``, which typically points into a local build tree. When
nothing is found, it returns ``None`` so that a driver can skip the API
reference.

``walk_stubs()``
----------------

.. code-block:: python

   walk_stubs(stub_dir: str, package: str) -> Iterator[tuple[str, str]]

This generator yields a ``(module name, stub path)`` pair for every stub
below ``stub_dir``. The ``package`` argument names the tree's top-level
module: ``pkg/sub/__init__.pyi`` maps to ``pkg.sub``, and ``pkg/io.pyi`` to
``pkg.io``. Directories starting with an underscore or dot are skipped, as
are stub files starting with an underscore, except ``__init__.pyi``.

``write_page()``
----------------

.. code-block:: python

   write_page(out_dir: str, slug: str, title: str, chunks: Iterable[list[str]],
              module: str, intro: Sequence[str] = ()) -> str

Each call writes one page ``<out_dir>/<slug>.rst`` and returns ``slug``.

- ``title``: the page heading.
- ``chunks``: the entry blocks to place on the page, in order.
- ``module``: emitted as a ``py:currentmodule`` directive, so that entries
  register under the right module and docstrings can cross-reference
  siblings by their short name.
- ``intro``: optional reStructuredText lines placed between the title and
  the first entry, typically a module docstring.

``write_text()``
----------------

.. code-block:: python

   write_text(path: str, text: str) -> None

This helper writes a file only when its content actually changed, creating
missing parent directories. An unchanged file keeps its mtime so that
Sphinx's incremental build does not re-read the corresponding page. ``write_page()``
and ``generate()`` route their output through it.

``toctree()``
-------------

.. code-block:: python

   toctree(title: str | None, slugs: Iterable[str], prefix: str = '',
           hidden: bool = False) -> str

This function returns a ``toctree`` of the given page slugs, for drivers
that emit their own linking structure. A ``title`` puts the tree under a
section heading, and ``prefix`` is prepended to every entry, where a prefix
starting with ``/`` resolves the entries against the documentation root,
which leaves the including page free to live anywhere. A ``hidden`` toctree
lists nothing and only tells Sphinx that the pages belong to the
documentation, which suits an internal module that exists so that
references to it resolve.

``generate()``
--------------

.. code-block:: python

   generate(stub_dir: str, out_dir: str, package: str,
            renderer: Renderer | None = None,
            sections: dict[str, list[str]] | None = None,
            warn: Callable[[str], None] | None = None) -> list[str]

``generate()`` runs the complete pipeline that the extension and the
command line use: it writes one alphabetical page per module plus an
``index.rst`` that links them, and returns the page slugs in index order.
A custom ``renderer`` substitutes rendering policy, a ``sections`` table
splits the top-level module thematically as described above, and ``warn``
reports anomalies in that table. Drivers whose page layout goes beyond the
sections mechanism skip this function and combine the pieces above instead.
