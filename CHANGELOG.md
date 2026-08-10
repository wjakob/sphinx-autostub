# Changelog

## 0.2.0

- `Renderer` accepts an `annotation_filter` that can be used to rewrite each
  type expression as text for further customization.

- `partition()` accepts a `warn` hook that reports anomalies in a sections
  table, e.g., a pattern that matches nothing, a name that several sections
  claim, and a name that no section claims and that is therefore dropped.

- `toctree()` accepts `hidden` for a tree that registers its pages without
  listing them, and its `title` may be `None` to emit the tree without a
  section heading.

- Properties now render their type as a `:type:` option, which a property
  signature would otherwise drop.

- The `Bases:` line of a class moves ahead of the class docstring.

## 0.1.0

Initial release.
