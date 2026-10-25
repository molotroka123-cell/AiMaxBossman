(function () {
  "use strict";

  // Sample videos: edit samples/samples.json, put files into samples/. Empty "src" = placeholder stays.
  fetch("/samples/samples.json", { cache: "no-cache" })
    .then(function (r) { return r.ok ? r.json() : { samples: [] }; })
    .then(function (data) {
      (data.samples || []).forEach(function (s) {
        if (!s || !s.src || !/^[a-z0-9-]+$/.test(s.preset || "")) return;
        var slot = document.querySelector('[data-slot="' + s.preset + '"]');
        if (!slot) return;
        var v = document.createElement("video");
        v.src = s.src;
        if (s.poster) v.poster = s.poster;
        v.muted = true; v.loop = true; v.playsInline = true; v.autoplay = true;
        v.setAttribute("playsinline", ""); v.preload = "metadata";
        v.setAttribute("aria-label", s.alt || "Ukázkové video (AI)");
        slot.textContent = "";
        slot.appendChild(v);
      });
    })
    .catch(function () { /* placeholders stay */ });

  var form = document.getElementById("lead");
  if (!form) return;
  var started = Date.now();
  var errBox = document.getElementById("form-err");
  var okBox = document.getElementById("form-ok");
  var btn = document.getElementById("submit");
  var hint = document.getElementById("contact-hint");
  var emailRe = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
  var phoneRe = /^[+0-9 ()-]{9,20}$/;

  function val(n) { return (form.elements[n].value || "").trim(); }
  function mark(n, bad) { form.elements[n].setAttribute("aria-invalid", bad ? "true" : "false"); }

  function validate() {
    var errors = [];
    var name = val("name"), biz = val("business"), email = val("email"), phone = val("phone"), link = val("link");
    mark("name", !name); if (!name) errors.push("Vyplňte jméno.");
    mark("business", !biz); if (!biz) errors.push("Vyplňte název firmy.");
    var emailBad = email && !emailRe.test(email);
    var phoneBad = phone && !phoneRe.test(phone);
    var noContact = !email && !phone;
    mark("email", emailBad || noContact); mark("phone", phoneBad || noContact);
    hint.classList.toggle("is-err", noContact);
    if (noContact) errors.push("Vyplňte e-mail nebo telefon.");
    if (emailBad) errors.push("E-mail nemá správný tvar.");
    if (phoneBad) errors.push("Telefon nemá správný tvar.");
    var linkBad = link && !/^(https?:\/\/)?[^\s]+\.[^\s]{2,}/i.test(link) && !/^@[\w.]{2,}$/.test(link);
    mark("link", !!linkBad); if (linkBad) errors.push("Odkaz nemá správný tvar.");
    var consent = form.elements.consent.checked;
    form.querySelector(".check").classList.toggle("is-err", !consent);
    if (!consent) errors.push("Pro odeslání je nutný souhlas se zpracováním údajů.");
    return errors;
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    okBox.hidden = true;
    var errors = validate();
    if (errors.length) {
      errBox.textContent = errors.join(" ");
      errBox.hidden = false;
      var first = form.querySelector('[aria-invalid="true"]');
      if (first) first.focus();
      return;
    }
    errBox.hidden = true;
    btn.disabled = true; btn.textContent = "Odesílám…";
    var payload = {
      name: val("name"), business: val("business"), email: val("email"), phone: val("phone"),
      link: val("link"), message: val("message"), website: val("website"),
      consent: true, elapsedMs: Date.now() - started
    };
    fetch("/api/lead", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        if (res.ok && res.j.ok) {
          form.reset();
          Array.prototype.forEach.call(form.querySelectorAll("[aria-invalid]"), function (el) { el.removeAttribute("aria-invalid"); });
          okBox.hidden = false;
        } else {
          errBox.textContent = res.j.error || "Odeslání se nepovedlo. Zkuste to prosím znovu za chvíli.";
          errBox.hidden = false;
        }
      })
      .catch(function () {
        errBox.textContent = "Odeslání se nepovedlo (chyba sítě). Zkuste to prosím znovu.";
        errBox.hidden = false;
      })
      .then(function () { btn.disabled = false; btn.textContent = "Odeslat poptávku"; });
  });
})();
