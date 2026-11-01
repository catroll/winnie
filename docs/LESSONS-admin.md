# Admin console lessons

What building `apps/admin` taught us. Read this before changing the console.

## AdminLTE's static layout assumes the body, a SPA has a root div

**Signal.** The login page looked right in the AdminLTE demo but sat top-left in
React, and the app shell's sidebar/footer did not span the viewport.

**Cause.** AdminLTE's pages are static HTML: `.app-wrapper` or `.login-box` is a
direct child of `<body>`, and `.login-page` uses `min-height: 100vh` on the
body itself. A React app puts `#root` there instead, so the flex centering has
no full-height parent to work with.

**Fix.** Keep AdminLTE's classes exactly as shipped and give `#root`
`min-height: 100vh`. The login page becomes a `<div class="login-page">` inside
the root rather than a body class. That is two CSS rules of application CSS; do
not fork the layout further.

**Check.** In a browser, `getComputedStyle(document.querySelector('.login-box')).width`
is `400px`, and the app footer is at the bottom of the viewport on a short page.

## A guard redirect can overwrite navigation state

**Signal.** After a successful password change the API revoked every session and
the browser returned to `/login`, but the "Password changed" notice disappeared.
The auth API had returned 204 and the new password worked.

**Cause.** Two navigations in one React batch. The account page called
`navigate('/login', { state: { notice } })`, then `RequireAuth` rendered its own
`<Navigate to="/login" state={{ from: location }} />` because the session was
already null. The guard's state won, so the login page had no notice.

**Fix.** Do not pass one-off auth messages through router location state while a
guard can also redirect to the same route. Keep the message in `AuthProvider`
(`flash`) so it survives whichever navigation wins, and clear it on the next
successful login. The browser check asserts the text is present on the login
page, not just the URL.

## SQLite returns meeting timestamps without a zone

**Signal.** A meeting created at `17:12Z` displayed as `17:12` instead of
`01:12` local time; `expires_at` from the same API displayed correctly.

**Cause.** `expires_at` is generated in Python with an aware UTC datetime and
serialised with `Z`. SQLite drops the offset on stored `DateTime(timezone=True)`
columns, so `meetings.created_at` arrives as `2026-10-31T17:12:31.197724` with
no designator. `new Date(...)` then interprets it as local time.

**Fix.** In `lib/format.ts`, append `Z` when the string has no `Z`/offset before
constructing a `Date`. The check is a regex on the end of the string, so a future
API that emits offsets keeps working.

## Do not screenshot a colour-mode switch immediately

**Signal.** The dark-mode screenshot showed a light grey header and footer with
washed-out text, while the cards were already dark. It read as a broken theme.

**Cause.** AdminLTE's header and footer have `transition: 0.3s ease-in-out`.
The screenshot was taken before the transition finished; `getComputedStyle`
reported the final dark values immediately.

**Check.** Wait at least 0.5 s or query the computed background colour. Do not
"fix" the theme from an early screenshot.

## Native `confirm` is dismissible by default under WebDriver

**Signal.** Clicking **Delete meeting** or **Regenerate minutes** in the test
browser left the page unchanged, and no alert was found.

**Cause.** Firefox's default `unhandledPromptBehavior` dismisses `window.confirm`
prompts automatically. The React handler correctly saw `false` and did nothing.

**Fix.** For manual checks, either accept the prompt deliberately
(`unhandledPromptBehavior: "ignore"`, then `GET /alert/text` and
`POST /alert/accept`), or set `unhandledPromptBehavior` to `accept`. When
changing these destructive actions, keep the test's prompt handling in mind:
"No dialog found" is not evidence the action did not fire.

## Firefox BiDi `setViewport` wants `context`, not `contexts`

**Signal.** The first chat verification run passed every interaction and then
failed at the screenshot step:

```
"invalid argument", "message": "At least one of \"context\" or \"userContexts\"
arguments should be provided"
```

The call was `browsingContext.setViewport` with `contexts: [context]`.

**Cause.** The installed Firefox 157 accepts the singular `context` (or
`userContexts`) for this command even though the sibling commands use
`contexts` in other revisions of the protocol. The error names the accepted
keys, so it is a parameter-shape problem, not a browser or session failure.

**Fix.** Use `{ context, viewport: { width, height }, devicePixelRatio: 1 }`.
`browsingContext.captureScreenshot` and `browsingContext.navigate` also take the
singular `context`, so one variable works for all three.

**Check.** Before the screenshot, assert the viewport actually changed by
reading `getComputedStyle` of a `vh`-sized element (the chat pane was `620px` at
1440×1000 and `523px` at 390×844). A failed `setViewport` would leave the
default 800×600 and the screenshot would still look plausible.

## A feature label also appears in the provider's "used by" badges

**Signal.** The browser test found a row containing `Assistant chat`, then
failed with `Save button is not clickable`. The page was correct; the row was
the `fake` provider, whose **Used by** column lists the same feature labels.

**Cause.** A page-level query
(`[...document.querySelectorAll('tbody tr')].find(row =>
row.innerText.includes('Assistant chat'))`) is ambiguous once a table elsewhere
shows the same text. The provider table's row has Edit/Delete, not Save.

**Fix.** Anchor to the control and walk up:
`document.querySelector('[id="binding-assistant.chat"]').closest('tr')`, then
search only that row. The same rule applies to any table that repeats a label in
another table: scope by the control's id, not by visible text.

**Check.** Before clicking, assert the control exists in the scoped row and
throw a named error when it does not. A missing button and a disabled button
produce the same "nothing happened" symptom, so report which one it was.

## AdminLTE remembers the colour mode between WebDriver sessions

**Signal.** A screenshot script that captured "light mode" produced a dark page,
and the same script's "dark mode" click changed nothing. The switcher still
worked when clicked by hand in a fresh profile.

**Cause.** AdminLTE persists the chosen colour mode (and the sidebar state) in
`localStorage`. The Firefox profile used for the check outlives the WebDriver
session, so the previous run's last theme was restored on load. Clicking
`[data-bs-theme-value=dark]` when the page was already dark is a no-op, so the
check could not tell the two states apart.

**Fix.** Set the mode explicitly at the start of every run and assert the
result instead of assuming the default:

```javascript
document.querySelector('[data-bs-theme-value=light]').click();
document.documentElement.getAttribute('data-bs-theme');       // "light"
getComputedStyle(document.body).backgroundColor;              // rgb(248, 249, 250)
```

Then switch to dark, wait ~0.5 s for the transition, and assert
`data-bs-theme="dark"` plus `rgb(43, 48, 53)` before the screenshot. A profile
under `.cache/ff/` is the right place for the session; clear it only when a
truly fresh state is the thing under test.

## A `<details>` panel inside a table cell resizes the whole table

**Signal.** The "Recent calls" columns looked roomy until one row's details were
opened; then the Feature and Provider columns wrapped (`Assistant chat`,
`deepseek-flash` split over two lines) and the table stayed squeezed while the
panel was open.

**Cause.** Auto table layout sizes a column to its widest cell. The opened
`<details>` contained a block with `minWidth: 16rem` plus a long JSON `<code>`,
so the Details column grew and every other column lost width. Closing it
restored the layout, which made it look like a transient rendering glitch.

**Fix.** Keep the trigger in the cell, render the content as a sibling
full-width row, and verify the column widths are identical before and after:

```javascript
// before opening
Array.from(table.querySelectorAll('thead th')).map(h => Math.round(h.getBoundingClientRect().width));
// after opening: same numbers
```

The trigger is a `<button aria-expanded>` rather than `<summary>`, and the panel
is `<tr class="table-light"><td colSpan={11}>`. Do not "fix" this by giving the
panel a fixed width; any width still competes with the other columns.

## A token renderer draws a type once per position, and `default: null` hides the miss

**Signal.** `- [x] done` in a rendered chat reply showed the text "done" with no
checkbox, and nothing logged. The GFM checkbox token *was* produced by `marked`
and the renderer had a `case 'checkbox'`.

**Cause.** The case was only in `inline()`. A task item's tokens are a flat list
on `list_item.tokens` — `[checkbox, text]` — so the checkbox is walked by
`blocks()`, whose `block()` switch had no such case and fell through to
`default: return null`. The marker disappeared silently because dropping an
unknown block is also the correct behaviour for `space`/`def`.

**Check.** Feed one sample of every token type at each nesting depth it can
appear at (top level, inside `list_item.tokens`, inside a table cell) and assert
the rendered element, not the parser output. In the browser the guard is a
count: `input[type=checkbox]:checked` must be 1 for one `- [x]` item.

**Fix.** One `taskCheckbox()` helper called from both switches, with the reason
written where the next reader will add a token type.

## `innerText` is empty for anything inside a closed `<details>`

**Signal.** A verification of a tool block asserted its `<pre>` content and got
`""`, although the same text was visible in the browser after clicking the
summary.

**Cause.** `innerText` is a *rendered* text value; content hidden by a closed
`<details>` has no layout box, so it reads as empty. `textContent` returns the
source text regardless.

**Fix.** Use `textContent` when asserting hidden content, and open the element
first when the assertion is about what a person sees. A probe that returns
`""` for text you know is stored is a hiding question, not a data question.

## A page-side probe written as a template literal cannot contain a backtick

**Signal.** A BiDi check of Markdown rendering failed to parse with `SyntaxError:
missing ) after argument list` at the closing backtick of a browser expression,
pointing at the last line of the block rather than the offending one.

**Cause.** The probe was passed to `script.evaluate` as a template literal that
also contained the Markdown sample, including a fenced code block. Its three
backticks ended the template literal, so the rest of the browser code was parsed
as module code — and the reported line is wherever the string finally closes.

**Fix.** Do not inline JavaScript into a template literal. Define the probe as an
ordinary page-side function and pass its source, which is parsed by Node (so an
error points at the real line), can contain any quoting, and stays readable:

```javascript
await evaluate(`JSON.stringify((${readBubble})())`);
```

Keep the function free of module-scope variables — it does not close over the
Node module it is defined in.

