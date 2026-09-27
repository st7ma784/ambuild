// New run page: "Insert" puts an uploaded file's reference into the recipe, replacing the
// selected text (e.g. a placeholder). Delegated, because htmx replaces the file list.
(function () {
  "use strict";
  document.addEventListener("click", function (event) {
    var button = event.target.closest("button.insert-ref");
    if (!button) return;
    var editor = document.getElementById("recipe");
    if (!editor) return;
    var ref = button.getAttribute("data-ref");
    var start = editor.selectionStart, end = editor.selectionEnd;
    var text = editor.value;
    // A selection of a whole "sha256:..." placeholder, or the cursor inside one, is replaced whole
    var before = text.lastIndexOf('"', start - 1), after = text.indexOf('"', end);
    if (start === end && before >= 0 && after >= 0 && text.slice(before + 1, before + 8) === "sha256:" &&
        text.slice(before + 1, after).indexOf("\n") < 0) {
      start = before + 1;
      end = after;
    }
    editor.setRangeText(ref, start, end, "end");
    editor.focus();
  });
})();
