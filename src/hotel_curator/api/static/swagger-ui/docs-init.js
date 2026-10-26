// Starts Swagger UI on the API's own docs page (no inline script, so the
// page's Content-Security-Policy can forbid inline code). URLs are relative
// to the page, so it works at /docs and behind the web proxy at /api/docs;
// "Try it out" requests go to the same prefix the page was served under.
window.addEventListener("load", function () {
  var prefix = new URL(".", window.location.href).pathname.replace(/\/$/, "");
  fetch("openapi.json")
    .then(function (response) {
      return response.json();
    })
    .then(function (spec) {
      spec.servers = [{ url: prefix || "/" }];
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
