# sphinx-autostub

This package generates a Sphinx API reference using only PEP 561 type stubs
(`*.pyi` files).

This may be of appeal to projects satisfying one or both of the following
points:

1. Significant effort was invested into making high-quality type stubs.
2. It's tricky to load the extension on services like [Read the
   Docs](https://about.readthedocs.com/) and use Sphinx'
   [autodoc](https://www.sphinx-doc.org/en/master/usage/extensions/autodoc.html)
   feature for generation. Causes could be that the package requires a GPU or
   is simply too costly to compile in the cloud.

This generator only needs stubs. It parses them with Python's builtin `ast`
module, accepts Google or NumPy style docstrings, and converts them to
reStructuredText via Sphinx's napoleon parser. It was developed for the
docstring-rich stubs that [nanobind](https://github.com/wjakob/nanobind)'s
stubgen produces, but the format is generic: pybind11-stubgen output and
hand-written stubs work as well. The generated pages use the `:no-index:`
option and therefore require Sphinx 7.2 or newer.

## Usage

The easiest way to use this package is as a Sphinx extension. For this, declare
the following in `conf.py`:

```python
extensions = ['sphinx_autostub']
autostub_packages = ['mypkg']
```

This renders the installed stubs of `mypkg` to `<srcdir>/api/mypkg/`, using one
page per module, plus an `index.rst` to place in a toctree. Documented
parameters that the stubs do not accept are reported as build warnings. Further
settings adjust the rendering:

```python
autostub_exclude = [r'_.*', 'detail']  # full-match regexes over unqualified names
autostub_style = 'numpy'               # 'google' (default), 'numpy', or 'rst'
autostub_sections = {                  # split the top-level module thematically
    'Widgets': ['Button', 'Label', r'Checkbox\w*'],
    'Application': [r'load_\w+', r'gui_\w+'],
}
```

A sections table groups the top-level module's contents into one page per
entry, in table order. Each name joins the first section with a matching regex,
leftovers land on an 'Other' page, and the submodules keep one page each. A
pattern that matches nothing, and a name that several sections claim, are
reported as build warnings.

The page-per-module layout is also available from the command line:

```
sphinx-autostub mypkg docs/api
sphinx-autostub path/to/stubs docs/api --style numpy
```

## Advanced usage

The package can also be used as a library to implement filtering and layout
policies beyond what the plain-data settings above can express. This mode
and the underlying interface are documented in
[docs/advanced.rst](docs/advanced.rst).

## License

BSD 3-clause. See the `LICENSE` file.
