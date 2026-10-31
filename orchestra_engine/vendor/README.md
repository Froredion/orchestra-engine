# Vendored browser libraries

The live viewer (`webui.html`) renders agent text as Markdown. Its CSP allows no
external hosts and the page must work offline, so these are inlined into the
page at serve time (`webui.py: _page`) rather than loaded from a CDN.

| File | Package | Source tarball integrity (npm registry) |
| --- | --- | --- |
| `marked.umd.js` | marked 18.0.14 (`lib/marked.umd.js`) | `sha512-mBHK6FBHuBAlhgRe88w9F0O1AbwwXJUcQibUbC/QcdTbVGAD7aWza+xt3N6oT/jCZx3/OMeS+8rnuiHZcQ9s7A==` |
| `purify.min.js` | dompurify 3.4.16 (`dist/purify.min.js`) | `sha512-sqo+pNp3qRhCIpbgRi1y8Tgk27Bo2Ry7w0dC1NBeNTdZChWjz9Xb/KOoZbRP/R6pQZ80Qw8YhXw13hWWBbMRnQ==` |

Copied unmodified apart from dropping the trailing `sourceMappingURL` line.
marked does not sanitize; every render goes through DOMPurify.

To update: download `https://registry.npmjs.org/<pkg>/-/<pkg>-<version>.tgz`,
check its sha512 against `https://registry.npmjs.org/<pkg>/<version>`
(`dist.integrity`), copy the same file out, and update this table.
