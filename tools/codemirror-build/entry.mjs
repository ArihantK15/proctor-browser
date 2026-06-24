// CodeMirror 6 editor for the kiosk coding question — bundled to a single IIFE
// (../vendor/codemirror.bundle.js) for offline file:// loading in the Electron
// kiosk. Exposes window.CMEditor.create().
//
// Deliberately LEAN for the low-end-laptop P0 gate and assessment integrity:
//   - NO autocomplete / IntelliSense (a crutch in a knowledge assessment, and
//     the heavy part of a rich editor). Only highlighting, line numbers,
//     bracket matching, history, sane editing.
//   - Highlighting for the full v1 language set: JS/TS (lang-javascript),
//     Python (lang-python), C/C++ (lang-cpp), Java (lang-java). Unknown langs
//     fall back to plain text (still editable).
import { EditorState } from "@codemirror/state";
import {
  EditorView, keymap, lineNumbers, highlightActiveLine,
  highlightActiveLineGutter, drawSelection
} from "@codemirror/view";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import {
  syntaxHighlighting, defaultHighlightStyle, bracketMatching, indentOnInput
} from "@codemirror/language";
import { javascript } from "@codemirror/lang-javascript";
import { python } from "@codemirror/lang-python";
import { cpp } from "@codemirror/lang-cpp";
import { java } from "@codemirror/lang-java";

function langExt(language) {
  const l = String(language || "").toLowerCase();
  if (l === "javascript" || l === "js") return javascript();
  if (l === "typescript" || l === "ts") return javascript({ typescript: true });
  if (l === "python" || l === "py") return python();
  if (l === "c" || l === "cpp" || l === "c++") return cpp();   // lang-cpp covers C + C++
  if (l === "java") return java();
  return []; // any other language → plain text, still usable
}

window.CMEditor = {
  /**
   * create(parent, { doc, language, readOnly, onChange }) -> handle
   * handle: { view, getValue(), setValue(s), setLanguage(l), focus(), destroy() }
   */
  create(parent, opts) {
    opts = opts || {};
    const langCompartmentExt = langExt(opts.language);

    const updateListener = EditorView.updateListener.of((u) => {
      if (u.docChanged && typeof opts.onChange === "function") {
        opts.onChange(u.state.doc.toString());
      }
    });

    const view = new EditorView({
      parent: parent,
      state: EditorState.create({
        doc: opts.doc || "",
        extensions: [
          lineNumbers(),
          highlightActiveLineGutter(),
          highlightActiveLine(),
          drawSelection(),
          history(),
          bracketMatching(),
          indentOnInput(),
          syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
          keymap.of([...defaultKeymap, ...historyKeymap, indentWithTab]),
          langCompartmentExt,
          EditorView.editable.of(opts.readOnly !== true),
          EditorView.theme({ "&": { height: "100%", fontSize: "13px" },
                             ".cm-scroller": { fontFamily: "ui-monospace, Menlo, monospace" } }),
          updateListener
        ]
      })
    });

    return {
      view: view,
      getValue() { return view.state.doc.toString(); },
      setValue(s) {
        view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: s || "" } });
      },
      focus() { view.focus(); },
      destroy() { view.destroy(); }
    };
  }
};
