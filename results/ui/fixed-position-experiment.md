# #25: does `container-type: inline-size` on `.trk` break `position: fixed` dialogs?

Question (JOURNAL spec-gap "`.trk` root uses container-type: inline-size"): from memory of the CSS containment spec I expected `.trk` to become
the containing block for `position: fixed` descendants. Tested with a long page (16 keys, page taller than the viewport) scrolled to the bottom,
then a dialog opened. Raw numbers copied from the tool output.

| Variant | Browser | Result |
|---|---|---|
| scrim (`position: fixed; inset: 0`) rendered naively inside the page's `.trk` | app pane Chromium, viewport 1000x700 | scrollY 228, `.trk` height 928, scrim top 0 and height 700 (= viewport, not `.trk`), dialog y 140..385: **fixed is relative to the viewport** |
| same, naive | Playwright Chromium 129.0.6668.29, 1280x720 | scrollY 190, doc height 910, scrim y 0 height 720, dialog y 140..386, dialog button receives the hit-test: **works** |
| dialog portalled to `<body>` inside a new wrapper `<div class="trk">` that is `position: fixed` (so it kept `container-type: inline-size`) | app pane Chromium | works (dialog y 140..385 at scrollY 228) |
| same wrapper | Playwright Chromium 129 | **broken**: `elementsFromPoint` over the dialog button returned only `HTML`, the screenshot showed none of the page or the dialog, although DOM and computed styles looked right (`pointer-events`, `visibility`, rects) |
| same wrapper with `container-type: normal` | Playwright Chromium 129 | works: button receives the hit-test; Playwright clicks succeed |

Conclusion for the journal: my memory-based claim (".trk becomes the containing block for fixed descendants") was wrong for these two Chromium builds.
Not tested: Firefox, Safari, other Chromium versions. The portal with a non-container wrapper is kept as a precaution, not because a failure of the naive version was seen.
