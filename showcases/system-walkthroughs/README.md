# System Walkthroughs showcase

This static collection is intended for `https://evanoman.com/system-walkthroughs/`.
`index.html` is the authored hub. The two guide pages are generated from the same
reusable renderer and builder shipped in `skills/system-walkthrough/`.

Regenerate both guides from the repository root with Node 22 or newer:

```sh
node showcases/system-walkthroughs/build.mjs
node showcases/system-walkthroughs/build.mjs --check
```

The input models are `skills/system-walkthrough/examples/valkey.json` and
`skills/system-walkthrough/examples/sqlite.json`. Edit the models, then regenerate;
do not maintain separate copies of diagram markup or interaction code in the output.
Each generated `index.html` embeds its model, CSS and renderer for offline reading.

Source pins:

| Guide | Upstream release | Commit |
| --- | --- | --- |
| Valkey consumer groups | 9.1.3 | `29173b67508b2de4c0f0ed551f3b47cf19fc3ca8` |
| SQLite WAL | 3.54.1 | `57520227c95396ce39ba6ecaac8d475b78dd3964` |

The guides contain original source paraphrases and illustrative scenarios. They are
AI-authored, with source links and limits in each document; they are not production
telemetry. Valkey source is BSD-3-Clause; SQLite's core source is public domain.
Upstream sources and documentation retain their own terms. The local MIT license
covers only the newly authored showcase files, not third-party material.

Publish this collection through the website's pinned static-project registry. Its
assembler should copy only `index.html`, `valkey/` and `sqlite/`; the build helper
and this README are authoring resources, not reading-time dependencies.
