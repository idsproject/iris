# HTML-to-PDF Renderer Fixes

Private, PDF-focused subset of our changes to your `aws-iris-layout.zip`.
Use this ZIP instead of the earlier browser-oriented handoffs.

## Changes

- Preserve BDA-classified tables with long text instead of turning them into
  prose, so the print HTML retains table structure for PDF tagging.
- Remove empty table row groups left behind when rebuilding header/body groups.
- Honor explicit heading levels when the input supplies them. Ordinary blocks
  marked `LOCAL_PARAGRAPH` are not promoted to headings again. This does not add
  new heading detection or change your standard BDA data automatically.
- Match existing image descriptions by a supplied figure ID as well as filename.
  This improves description transfer when those identifiers are available; you can then choose
  generate alt text or verify its accuracy.
- Read UTF-8/BOM BDA JSON reliably, write text explicitly as UTF-8, and close the
  main/background PDF documents on normal completion.
- Several minor changes to html tagging to promote optimal reading order, particularly around tables.

## Apply

Replace your `src/iris_layout.py` with the included copy, or apply
`iris_layout.patch` from the root of your original project after running
`git apply --check iris_layout.patch`.

Keep your existing handler, fonts, dependencies, deployment scripts and Prince
conversion command. The existing arguments and `build` entry point are retained.
For example, using your existing fonts and a matching PDF/BDA pair:

```sh
python src/iris_layout.py original.pdf result.json -o preview.html \
  --remediated remediated.html --print-out print.html --fonts src/fonts
```

Pass `print.html`, not the browser preview, to your existing HTML-to-PDF process.
The patch is based on the exact original renderer you supplied; check it before
applying to a newer revision. No cloud configuration or new dependencies are
included. The updated file has no new local-module imports.
