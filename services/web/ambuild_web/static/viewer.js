// Structure viewer for a run page: the run's checkpoints (extended XYZ written by
// Cell.dump(), or plain XYZ for older runs) drawn with 3Dmol.js. Markup: a
// #structure-viewer element holding .viewer-canvas and the controls, and
// <script type="application/json" id="structure-data">{"box": [A, B, C],
// "frames": [{"step": 3, "url": "...", "ion_maps": [{"ion": "Li+", "map": "...", "cube": "...",
// "escape_energy": -0.3, "tier": "..."}]}]}</script>. A frame's ion maps (liminal's map.json
// and energy.cube) are drawn over it: an energy surface, the sites and the crossing paths.
(function () {
  "use strict";
  var root = document.getElementById("structure-viewer");
  var source = document.getElementById("structure-data");
  if (!root || !source) return;
  if (typeof $3Dmol === "undefined") {
    root.querySelector(".viewer-status").textContent = "The viewer needs JavaScript and WebGL.";
    return;
  }
  var spec = JSON.parse(source.textContent);
  var frames = spec.frames;
  var LARGE = 10000; // atoms: draw lines, not sticks
  var PALETTE_VARS = ["--accent", "--series2", "--series3", "--series4", "--series5", "--series6"];

  var canvas = root.querySelector(".viewer-canvas");
  var slider = root.querySelector("#frame");
  var stepLabel = root.querySelector("#frame-step");
  var colourSelect = root.querySelector("#colour-by");
  var styleSelect = root.querySelector("#style");
  var boxCheck = root.querySelector("#show-box");
  var fragmentsBox = root.querySelector(".viewer-fragments");
  var status = root.querySelector(".viewer-status");
  // Ion maps (liminal: map.json and energy.cube, docs/ion-maps.md), per frame
  var ionsBox = root.querySelector(".viewer-ions");
  var ionSelect = root.querySelector("#ion-map");
  var ionSurface = root.querySelector("#ion-surface");
  var ionLevel = root.querySelector("#ion-level");
  var ionSites = root.querySelector("#ion-sites");
  var ionPaths = root.querySelector("#ion-paths");
  var ionTier = root.querySelector(".ion-tier");
  var maps = {}; // map url -> {map, cube} once loaded, or "loading"

  function cssVar(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }

  var viewer = $3Dmol.createViewer(canvas, {backgroundColor: cssVar("--panel", "#ffffff")});
  var cache = {};
  var current = null;
  var firstDraw = true;
  var hidden = {}; // fragment type -> true when hidden

  function parse(text) {
    var lines = text.split(/\r?\n/);
    var n = parseInt(lines[0], 10);
    var header = lines[1] || "";
    var lattice = null;
    var m = header.match(/Lattice="([^"]+)"/);
    if (m) {
      var v = m[1].trim().split(/\s+/).map(Number);
      lattice = [v[0], v[4], v[8]];
    }
    var cols = {species: 0, x: 1, y: 2, z: 3};
    var pm = header.match(/Properties=(\S+)/);
    if (pm) {
      var parts = pm[1].split(":");
      var col = 0;
      cols = {};
      for (var i = 0; i + 2 < parts.length; i += 3) {
        var count = parseInt(parts[i + 2], 10);
        if (parts[i] === "pos") { cols.x = col; cols.y = col + 1; cols.z = col + 2; } else { cols[parts[i]] = col; }
        col += count;
      }
    }
    var atoms = [];
    for (var j = 2; j < 2 + n && j < lines.length; j++) {
      var f = lines[j].trim().split(/\s+/);
      if (f.length < 4) continue;
      atoms.push({
        elem: f[cols.species],
        x: parseFloat(f[cols.x]), y: parseFloat(f[cols.y]), z: parseFloat(f[cols.z]),
        fragment: cols.fragment !== undefined ? f[cols.fragment] : "",
        block: cols.block !== undefined ? f[cols.block] : ""
      });
    }
    return {atoms: atoms, lattice: lattice};
  }

  function load(i) {
    if (cache[i]) return Promise.resolve(cache[i]);
    return fetch(frames[i].url).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.text();
    }).then(function (text) {
      cache[i] = parse(text);
      return cache[i];
    });
  }

  // A colour per fragment type or block: the theme's series colours, then golden-angle hues
  var colourIndex = {};
  function colourFor(key) {
    if (!(key in colourIndex)) colourIndex[key] = Object.keys(colourIndex).length;
    var i = colourIndex[key];
    if (i < PALETTE_VARS.length) return cssVar(PALETTE_VARS[i], "#0b6f7f");
    return "hsl(" + Math.round((i * 137.508) % 360) + ", 55%, 50%)";
  }

  function updateFragments(frame) {
    var types = {};
    frame.atoms.forEach(function (a) { if (a.fragment) types[a.fragment] = true; });
    var names = Object.keys(types).sort();
    var existing = Array.prototype.map.call(fragmentsBox.querySelectorAll("input"), function (el) { return el.value; });
    if (names.join("|") === existing.join("|")) return;
    fragmentsBox.innerHTML = "";
    if (!names.length) return;
    var legend = document.createElement("span");
    legend.className = "note";
    legend.textContent = "Fragments:";
    fragmentsBox.appendChild(legend);
    names.forEach(function (name) {
      var label = document.createElement("label");
      label.className = "check";
      var box = document.createElement("input");
      box.type = "checkbox";
      box.value = name;
      box.checked = !hidden[name];
      box.addEventListener("change", function () { hidden[name] = !box.checked; draw(); });
      label.appendChild(box);
      label.appendChild(document.createTextNode(" " + name));
      fragmentsBox.appendChild(label);
    });
  }

  // The cell's 12 edges (3Dmol's wireframe box would also draw each face's diagonal)
  function drawCell(dims) {
    var colour = cssVar("--muted", "#5d676c");
    var corner = function (i, j, k) { return {x: i * dims[0], y: j * dims[1], z: k * dims[2]}; };
    var edges = [
      [[0, 0, 0], [1, 0, 0]], [[0, 1, 0], [1, 1, 0]], [[0, 0, 1], [1, 0, 1]], [[0, 1, 1], [1, 1, 1]],
      [[0, 0, 0], [0, 1, 0]], [[1, 0, 0], [1, 1, 0]], [[0, 0, 1], [0, 1, 1]], [[1, 0, 1], [1, 1, 1]],
      [[0, 0, 0], [0, 0, 1]], [[1, 0, 0], [1, 0, 1]], [[0, 1, 0], [0, 1, 1]], [[1, 1, 0], [1, 1, 1]]
    ];
    edges.forEach(function (e) {
      viewer.addLine({start: corner.apply(null, e[0]), end: corner.apply(null, e[1]), color: colour, dashed: false});
    });
  }

  function selectedIonMap() {
    if (!current || !ionsBox) return null;
    var entries = frames[current.index].ion_maps || [];
    return entries.filter(function (e) { return e.ion === ionSelect.value; })[0] || null;
  }

  function updateIons(i) {
    if (!ionsBox) return;
    var entries = frames[i].ion_maps || [];
    ionsBox.hidden = !entries.length;
    var names = entries.map(function (e) { return e.ion; });
    var existing = Array.prototype.slice.call(ionSelect.options, 1).map(function (o) { return o.value; });
    if (names.join("|") !== existing.join("|")) {
      // keep the ion shown; else the address's (?ion=K%2B, or ?ion= for none); else the first
      var keep = ionSelect.options.length > 1 ? ionSelect.value : urlIon;
      ionSelect.length = 1;
      names.forEach(function (name) { ionSelect.add(new Option(name, name)); });
      ionSelect.value = keep === "" || names.indexOf(keep) >= 0 ? keep : (names[0] || "");
      ionChanged();
    }
  }

  function ionChanged() {
    var entry = selectedIonMap();
    if (entry && entry.escape_energy !== null && entry.escape_energy !== undefined) {
      ionLevel.value = (Math.round(entry.escape_energy * 100) / 100).toString();
    }
    ionTier.textContent = entry && entry.tier ? entry.tier : "";
  }

  // The map's files, fetched once; draw() runs again when they arrive
  function loadedMap(entry) {
    var cached = maps[entry.map];
    if (cached && cached !== "loading") return cached;
    if (!cached) {
      maps[entry.map] = "loading";
      var get = function (url, json) {
        return url ? fetch(url).then(function (r) {
          if (!r.ok) throw new Error("HTTP " + r.status);
          return json ? r.json() : r.text();
        }) : Promise.resolve(null);
      };
      Promise.all([get(entry.map, true), get(entry.cube, false)]).then(function (both) {
        maps[entry.map] = {map: both[0], cube: both[1]};
        draw();
      }).catch(function (err) {
        delete maps[entry.map];
        status.textContent = "Could not load the " + entry.ion + " ion map: " + err.message;
      });
    }
    return null;
  }

  function wrap(p, dims) {
    return {x: ((p[0] % dims[0]) + dims[0]) % dims[0], y: ((p[1] % dims[1]) + dims[1]) % dims[1],
            z: ((p[2] % dims[2]) + dims[2]) % dims[2]};
  }

  function drawIonMap(loaded, dims) {
    var accent = cssVar("--ok", "#2e7d32"), trapped = cssVar("--fail", "#c62828");
    var surface = cssVar("--series2", "#e07b39"), pathColour = cssVar("--accent", "#0b6f7f");
    var level = parseFloat(ionLevel.value);
    if (ionSurface.checked && loaded.cube && !isNaN(level)) {
      viewer.addVolumetricData(loaded.cube, "cube", {isoval: level, color: surface, opacity: 0.6});
    }
    // Each layer is one shape: a shape per sphere or segment is too heavy to draw
    var sites = (loaded.map && loaded.map.sites) || [];
    if (ionSites.checked && sites.length) {
      var lowest = sites.reduce(function (a, s) { return a === null || s.energy < a.energy ? s : a; }, null);
      var siteShape = viewer.addShape({color: accent});
      sites.slice(0, 2000).forEach(function (s) {
        siteShape.addSphere({center: {x: s.position[0], y: s.position[1], z: s.position[2]},
                             radius: s === lowest ? 0.6 : 0.3, color: s.barrier === null ? trapped : accent});
      });
    }
    var paths = (loaded.map && loaded.map.paths) || [];
    if (ionPaths.checked && dims && paths.length) {
      // lines, and a sphere at each bottleneck: 20 paths have thousands of points, and
      // spheres or tubes along them (with the sites' spheres) were too many to draw
      var pathShape = viewer.addShape({color: pathColour});
      paths.forEach(function (path) {
        for (var n = 1; n < path.points.length; n++) {
          var b = wrap(path.points[n], dims);
          var a = wrap(path.points[n - 1], dims);
          var jump = Math.abs(a.x - b.x) > dims[0] / 2 || Math.abs(a.y - b.y) > dims[1] / 2 ||
            Math.abs(a.z - b.z) > dims[2] / 2;
          if (!jump) pathShape.addLine({start: a, end: b, color: pathColour});
        }
        pathShape.addSphere({center: wrap(path.bottleneck.position, dims), radius: 0.45, color: trapped});
      });
    }
  }

  function draw() {
    if (!current) return;
    var frame = current.frame;
    var shown = frame.atoms.filter(function (a) { return !hidden[a.fragment]; });
    var view = firstDraw ? null : viewer.getView();
    viewer.clear();
    if (shown.length) {
      var xyz = shown.length + "\n\n" + shown.map(function (a) {
        return a.elem + " " + a.x + " " + a.y + " " + a.z;
      }).join("\n");
      var model = viewer.addModel(xyz, "xyz");
      model.selectedAtoms({}).forEach(function (atom, i) {
        atom.fragment = shown[i].fragment;
        atom.blockId = shown[i].block;
      });
      var style = styleSelect.value === "auto" ? (shown.length > LARGE ? "line" : "stick") : styleSelect.value;
      var colour = colourSelect.value;
      var extra = {};
      if (colour === "fragment") extra.colorfunc = function (atom) { return colourFor("f:" + atom.fragment); };
      if (colour === "block") extra.colorfunc = function (atom) { return colourFor("b:" + atom.blockId); };
      var styleSpec = {};
      if (style === "line") styleSpec.line = Object.assign({}, extra);
      if (style === "stick") {
        styleSpec.stick = Object.assign({radius: 0.12}, extra);
        styleSpec.sphere = Object.assign({scale: 0.22}, extra);
      }
      if (style === "sphere") styleSpec.sphere = Object.assign({scale: 0.8}, extra);
      model.setStyle({}, styleSpec);
    }
    var dims = frame.lattice || spec.box;
    if (boxCheck.checked && dims) drawCell(dims);
    var ionEntry = selectedIonMap();
    var ionNote = "";
    if (ionEntry) {
      var loaded = loadedMap(ionEntry);
      if (loaded) {
        drawIonMap(loaded, dims);
        ionNote = " · " + ionEntry.ion + " map";
      } else {
        ionNote = " · loading the " + ionEntry.ion + " map…";
      }
    }
    if (view) { viewer.setView(view); } else { viewer.zoomTo(); firstDraw = false; }
    viewer.render();
    status.textContent = shown.length.toLocaleString() + " atoms shown of " + frame.atoms.length.toLocaleString() +
      (current.step !== null && current.step !== undefined ? " · step " + current.step : "") +
      " · " + frames[current.index].path + ionNote;
  }

  function show(i) {
    stepLabel.textContent = frames[i].step !== null ? frames[i].step : "–";
    status.textContent = "Loading " + frames[i].path + "…";
    load(i).then(function (frame) {
      current = {index: i, step: frames[i].step, frame: frame};
      updateFragments(frame);
      updateIons(i);
      draw();
    }).catch(function (err) {
      status.textContent = "Could not load " + frames[i].path + ": " + err.message;
    });
  }

  // Options from the address (?colour=fragment&style=sphere), so a view can be shared as a link
  var params = new URLSearchParams(window.location.search);
  var urlIon = params.get("ion"); // null: not given
  if (ionsBox && params.get("layers") !== null) { // e.g. ?layers=sites,paths
    var layers = params.get("layers").split(",");
    [[ionSurface, "surface"], [ionSites, "sites"], [ionPaths, "paths"]].forEach(function (pair) {
      pair[0].checked = layers.indexOf(pair[1]) >= 0;
    });
  }
  [[colourSelect, "colour"], [styleSelect, "style"]].forEach(function (pair) {
    var value = params.get(pair[1]);
    if (value && Array.prototype.some.call(pair[0].options, function (o) { return o.value === value; })) {
      pair[0].value = value;
    }
  });

  slider.max = String(frames.length - 1);
  slider.value = String(frames.length - 1);
  slider.disabled = frames.length < 2;
  slider.addEventListener("input", function () { show(parseInt(slider.value, 10)); });
  [colourSelect, styleSelect, boxCheck].forEach(function (el) { el.addEventListener("change", draw); });
  if (ionsBox) {
    ionSelect.addEventListener("change", function () { ionChanged(); draw(); });
    [ionSurface, ionLevel, ionSites, ionPaths].forEach(function (el) { el.addEventListener("change", draw); });
  }

  root.querySelector("#viewer-fullscreen").addEventListener("click", function () {
    if (document.fullscreenElement) { document.exitFullscreen(); }
    else if (root.requestFullscreen) { root.requestFullscreen(); }
  });
  document.addEventListener("fullscreenchange", function () { viewer.resize(); viewer.render(); });
  window.addEventListener("resize", function () { viewer.resize(); viewer.render(); });
  root.querySelector("#viewer-screenshot").addEventListener("click", function () {
    var link = document.createElement("a");
    link.href = viewer.pngURI();
    link.download = "structure-step-" + (current ? current.step : "") + ".png";
    document.body.appendChild(link);
    link.click();
    link.remove();
  });

  show(frames.length - 1);
})();
