// Pure, DOM-free decision used by applyOrgRole() (dashboard-app.js) to hide
// a sidebar section header when every item inside it has been role-gated
// to display:none — otherwise a plain org-teacher sees an empty
// "Organization" header with nothing underneath it. No DOM access in this
// file on purpose: it's the one piece of new logic in the nav redesign, so
// it's the one piece worth a real unit test (via `node --test`, no browser
// needed).
function _groupShouldBeVisible(displayValues) {
  return displayValues.some(function (d) { return d !== 'none'; });
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { _groupShouldBeVisible };
}
