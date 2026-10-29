// Starts Swagger UI on the API's own docs page (no inline script, so the
// page's Content-Security-Policy can forbid inline code). The page is served
// at /docs by the API and at /api/docs behind the web proxy; "Try it out"
// requests go to that prefix on this origin. The prefix comes from this
// list, never from the address bar: nginx serves a crafted path such as
// //other.example/..%2Fapi/docs as /api/docs, and read back, that path
// would make the request URL another host.
var DOCS_PREFIXES = { "/docs": "", "/api/docs": "/api" };

window.addEventListener("load", function () {
  var path = window.location.pathname;
  var prefix = Object.prototype.hasOwnProperty.call(DOCS_PREFIXES, path)
    ? DOCS_PREFIXES[path]
    : "/api";
  fetch("openapi.json")
    .then(function (response) {
      return response.json();
    })
    .then(function (spec) {
      spec.servers = [{ url: window.location.origin + prefix }];
      window.ui = SwaggerUIBundle({
        spec: spec,
        dom_id: "#swagger-ui",
        validatorUrl: null,
        deepLinking: true,
        presets: [SwaggerUIBundle.presets.apis],
        layout: "BaseLayout",
      });
    });
});
