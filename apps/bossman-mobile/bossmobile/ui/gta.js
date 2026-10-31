/* GTA: Днепр 3D · BossBlock Mini App Engine (Three.js r128 + Real Dnipro GeoJSON/Satellite/StreetView/GLB) */
(function () {
  "use strict";

  // Force GLTFLoader to use THREE.TextureLoader (<img>) instead of ImageBitmapLoader (fetch blob:)
  // so embedded GLB textures comply with strict CSP (img-src 'self' data: blob:; connect-src 'self').
  try {
    window.createImageBitmap = undefined;
  } catch (_) {}

  const BOUNDS = {
    south: 48.4638,
    west: 35.0448,
    north: 48.4658,
    east: 35.0480,
    centerLat: 48.4648,
    centerLon: 35.0464,
    widthM: 235.9,
    heightM: 222.4,
  };

  function gpsToWorld(lat, lon) {
    const x = ((lon - BOUNDS.centerLon) / (BOUNDS.east - BOUNDS.west)) * BOUNDS.widthM;
    const z = -((lat - BOUNDS.centerLat) / (BOUNDS.north - BOUNDS.south)) * BOUNDS.heightM;
    return { x, z };
  }

  function worldToGps(x, z) {
    const lon = BOUNDS.centerLon + (x / BOUNDS.widthM) * (BOUNDS.east - BOUNDS.west);
    const lat = BOUNDS.centerLat - (z / BOUNDS.heightM) * (BOUNDS.north - BOUNDS.south);
    return { lat, lon };
  }

  const LANDMARKS = {
    tsum: { name: "ЦУМ & Ротонда", x: -18, z: -22, street: "пр. Дмитра Яворницького, 52 · ЦУМ" },
    europa: { name: "ТЦ Європа (Европейская пл.)", x: 8, z: 4, street: "Європейська площа · ТЦ Європа" },
    carousel: { name: "Французька карусель", x: -6, z: -5, street: "вул. Центральна · Карусель" },
    yavornytskogo: { name: "пр. Дмитра Яворницького", x: -42, z: -44, street: "проспект Дмитра Яворницького" },
    scorpion: { name: "ТЦ Скорпіон", x: -48, z: 38, street: "вул. Володимира Мономаха · Скорпіон" },
  };

  const state = {
    mode: "walk", // "walk" | "drive"
    auraText: "BOSSMAN · ДНЕПР 2026",
    auraColor: "#00f5d4",
    textureMode: "realism", // "realism" | "neon" | "gold" | "streetproj" | "wireframe"
    cash: 250000,
    wanted: 5,
    cameraPreset: 0, // 0: GTA Follow, 1: Close Aura Inspect, 2: Drone Birdseye
    cameraYaw: 2.35,
    cameraPitch: 0.14,
    player: {
      x: -22,
      z: -24,
      yaw: -0.78,
      speed: 0,
      walkPhase: 0,
      boostTimer: 0,
      pulseScale: 1,
    },
    car: {
      x: -18,
      z: -20,
      yaw: -0.78,
      speed: 0,
      steer: 0,
    },
    joy: { active: false, dx: 0, dy: 0, pointerId: null },
    keys: {},
    buildings: [],
    buildingMeshes: [],
    billboardSprites: [],
    roads: [],
    streetPhotos: [],
    currentPhotoIdx: 0,
    checkpoints: [
      { id: "cp_tsum", title: "ЦУМ Днепр", x: -16, z: -18, done: false },
      { id: "cp_carousel", title: "Французская карусель", x: -5, z: -4, done: false },
      { id: "cp_europa", title: "ТЦ Європа", x: 14, z: 8, done: false },
      { id: "cp_yav", title: "пр. Дмитра Яворницького", x: -45, z: -42, done: false },
      { id: "cp_scorpion", title: "ТЦ Скорпіон", x: -44, z: 32, done: false },
    ],
    meshLoaded: false,
    meshVisible: false,
    meshObject: null,
    satelliteImg: null,
  };

  // DOM Elements
  const worldCanvas = document.getElementById("worldCanvas");
  const radarCanvas = document.getElementById("radarCanvas");
  const radarCtx = radarCanvas.getContext("2d");
  const joystickCanvas = document.getElementById("joystickCanvas");
  const joyCtx = joystickCanvas.getContext("2d");

  const hudStreetName = document.getElementById("hudStreetName");
  const hudMissionText = document.getElementById("hudMissionText");
  const hudCash = document.getElementById("hudCash");
  const hudModeLabel = document.getElementById("hudModeLabel");
  const hudSpeedLabel = document.getElementById("hudSpeedLabel");
  const hudCoordsLabel = document.getElementById("hudCoordsLabel");
  const gtaBanner = document.getElementById("gtaBanner");
  const gtaBannerTitle = document.getElementById("gtaBannerTitle");
  const gtaBannerSub = document.getElementById("gtaBannerSub");

  const auraDrawer = document.getElementById("auraDrawer");
  const auraTextInput = document.getElementById("auraTextInput");
  const streetModal = document.getElementById("streetModal");
  const svTitle = document.getElementById("svTitle");
  const svMeta = document.getElementById("svMeta");
  const svImage = document.getElementById("svImage");

  // Three.js Scene Setup
  const renderer = new THREE.WebGLRenderer({
    canvas: worldCanvas,
    antialias: true,
    preserveDrawingBuffer: true,
    powerPreference: "high-performance",
  });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(window.innerWidth, window.innerHeight);
  renderer.shadowMap.enabled = true;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x070d19);
  scene.fog = new THREE.FogExp2(0x070d19, 0.0032);

  const camera = new THREE.PerspectiveCamera(58, window.innerWidth / window.innerHeight, 0.5, 900);

  // Lighting
  const hemiLight = new THREE.HemisphereLight(0xa5f3fc, 0x0f172a, 0.95);
  scene.add(hemiLight);

  const dirLight = new THREE.DirectionalLight(0xfef08a, 1.15);
  dirLight.position.set(85, 140, 65);
  scene.add(dirLight);

  const neonPointLight = new THREE.PointLight(0x00f5d4, 2.4, 38);
  neonPointLight.position.set(0, 6, 0);
  scene.add(neonPointLight);

  // Sky Star & Drone Dome
  const skyGroup = new THREE.Group();
  scene.add(skyGroup);
  (function buildSky() {
    const starGeo = new THREE.BufferGeometry();
    const pts = [];
    for (let i = 0; i < 400; i++) {
      const r = 260 + Math.random() * 140;
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.random() * Math.PI * 0.46;
      pts.push(r * Math.cos(theta) * Math.sin(phi), r * Math.cos(phi) + 20, r * Math.sin(theta) * Math.sin(phi));
    }
    starGeo.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
    const starMat = new THREE.PointsMaterial({ color: 0x7dd3fc, size: 1.6, transparent: true, opacity: 0.75 });
    skyGroup.add(new THREE.Points(starGeo, starMat));
  })();

  // Ground Plane (Dnipro Satellite Ortho 235.9m x 222.4m)
  const texLoader = new THREE.TextureLoader();
  const groundGeo = new THREE.PlaneGeometry(BOUNDS.widthM, BOUNDS.heightM, 32, 32);
  groundGeo.rotateX(-Math.PI / 2);
  const groundMat = new THREE.MeshStandardMaterial({
    color: 0xffffff,
    roughness: 0.82,
    metalness: 0.12,
  });
  const groundMesh = new THREE.Mesh(groundGeo, groundMat);
  groundMesh.receiveShadow = true;
  scene.add(groundMesh);

  // Subtle Cyber Grid Overlay on Ground
  const gridHelper = new THREE.GridHelper(260, 52, 0x00f5d4, 0x1e293b);
  gridHelper.position.y = 0.06;
  gridHelper.material.transparent = true;
  gridHelper.material.opacity = 0.22;
  scene.add(gridHelper);

  // Load Satellite Texture
  texLoader.load("dnipro_assets/satellite.png", function (tex) {
    tex.anisotropy = 4;
    groundMat.map = tex;
    groundMat.needsUpdate = true;
    state.satelliteImg = tex.image;
  });

  // Procedural Facade Window Texture Generator
  function makeFacadeTexture(mode, accentHex, customImage) {
    const c = document.createElement("canvas");
    c.width = 256;
    c.height = 256;
    const ctx = c.getContext("2d");

    if (customImage) {
      ctx.drawImage(customImage, 0, 0, 256, 256);
      ctx.fillStyle = "rgba(6, 12, 24, 0.22)";
      ctx.fillRect(0, 0, 256, 256);
    } else if (mode === "gold") {
      const g = ctx.createLinearGradient(0, 0, 0, 256);
      g.addColorStop(0, "#422006");
      g.addColorStop(1, "#1c1917");
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, 256, 256);
    } else if (mode === "neon") {
      ctx.fillStyle = "#080f22";
      ctx.fillRect(0, 0, 256, 256);
    } else {
      const g = ctx.createLinearGradient(0, 0, 0, 256);
      g.addColorStop(0, "#1e293b");
      g.addColorStop(1, "#0f172a");
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, 256, 256);
    }

    // Windows grid
    const cols = 6;
    const rows = 8;
    for (let r = 0; r < rows; r++) {
      for (let col = 0; col < cols; col++) {
        const lit = (r * 7 + col * 13) % 5 !== 0;
        if (mode === "neon") {
          ctx.fillStyle = lit ? accentHex : "rgba(15, 23, 42, 0.9)";
        } else if (mode === "gold") {
          ctx.fillStyle = lit ? "#facc15" : "#292524";
        } else {
          ctx.fillStyle = lit ? "#fde68a" : "#334155";
        }
        ctx.fillRect(10 + col * 40, 10 + r * 30, 24, 18);
      }
    }

    // Horizontal architectural ledges
    ctx.strokeStyle = mode === "neon" ? accentHex : "rgba(148, 163, 184, 0.35)";
    ctx.lineWidth = 2;
    for (let r = 0; r <= rows; r++) {
      ctx.beginPath();
      ctx.moveTo(0, r * 32);
      ctx.lineTo(256, r * 32);
      ctx.stroke();
    }

    const t = new THREE.CanvasTexture(c);
    t.wrapS = THREE.RepeatWrapping;
    t.wrapT = THREE.RepeatWrapping;
    return t;
  }

  let sharedFacadeTex = makeFacadeTexture("realism", state.auraColor, null);

  // Create Dynamic 3D Aura Text Banner Sprite
  function createTextBannerTexture(text, subtext, accentColor, isCharacterAura) {
    const canvas = document.createElement("canvas");
    canvas.width = 1024;
    canvas.height = 256;
    const ctx = canvas.getContext("2d");

    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // Rounded Cyber Frame
    const pad = 18;
    const w = canvas.width - pad * 2;
    const h = canvas.height - pad * 2;
    ctx.fillStyle = isCharacterAura ? "rgba(5, 10, 22, 0.82)" : "rgba(6, 11, 25, 0.90)";
    ctx.strokeStyle = accentColor;
    ctx.lineWidth = 8;

    ctx.beginPath();
    ctx.roundRect(pad, pad, w, h, 36);
    ctx.fill();
    ctx.stroke();

    // Inner Neon Scanline Stripes
    ctx.fillStyle = "rgba(255, 255, 255, 0.04)";
    for (let y = pad + 10; y < pad + h - 10; y += 12) {
      ctx.fillRect(pad + 12, y, w - 24, 4);
    }

    // Glow & Main Aura Typography
    ctx.save();
    ctx.shadowColor = accentColor;
    ctx.shadowBlur = 24;
    ctx.fillStyle = "#ffffff";
    ctx.font = "900 66px -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(text.toUpperCase(), canvas.width / 2, canvas.height / 2 - 18);
    ctx.restore();

    // Subtitle Tag
    ctx.fillStyle = accentColor;
    ctx.font = "800 30px ui-monospace, SFMono-Regular, Consolas, monospace";
    ctx.textAlign = "center";
    ctx.fillText(subtext, canvas.width / 2, canvas.height / 2 + 54);

    return new THREE.CanvasTexture(canvas);
  }

  // Create Ring Texture for Ground Aura
  function createAuraFloorTexture(accentColor) {
    const c = document.createElement("canvas");
    c.width = 512;
    c.height = 512;
    const ctx = c.getContext("2d");
    const cx = 256;
    const cy = 256;

    const grad = ctx.createRadialGradient(cx, cy, 30, cx, cy, 245);
    grad.addColorStop(0, "rgba(0,0,0,0)");
    grad.addColorStop(0.55, accentColor + "33");
    grad.addColorStop(0.82, accentColor + "cc");
    grad.addColorStop(0.95, "#ffffff");
    grad.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.arc(cx, cy, 248, 0, Math.PI * 2);
    ctx.fill();

    // Segmented Cyber Arcs & Text on Ground Aura
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 6;
    for (let i = 0; i < 12; i++) {
      const a0 = (i * Math.PI) / 6;
      ctx.beginPath();
      ctx.arc(cx, cy, 210, a0, a0 + 0.32);
      ctx.stroke();
    }

    // Circular Aura Text on the floor ring
    ctx.save();
    ctx.translate(cx, cy);
    ctx.fillStyle = "#ffffff";
    ctx.font = "900 24px sans-serif";
    ctx.textAlign = "center";
    const ringLabel = (state.auraText + " • ").repeat(3);
    for (let i = 0; i < ringLabel.length; i++) {
      const angle = (i / ringLabel.length) * Math.PI * 2;
      ctx.save();
      ctx.rotate(angle);
      ctx.fillText(ringLabel[i], 0, -168);
      ctx.restore();
    }
    ctx.restore();

    return new THREE.CanvasTexture(c);
  }

  // Build 3D Player Avatar (Smooth LatheGeometry + Limbs + Aura Halo + Floating Aura Text)
  const playerGroup = new THREE.Group();
  scene.add(playerGroup);

  const avatarBodyGroup = new THREE.Group();
  playerGroup.add(avatarBodyGroup);

  // Smooth Lathe Torso (Argon standard)
  const lathePoints = [
    new THREE.Vector2(0.01, 0.65),
    new THREE.Vector2(0.32, 0.72),
    new THREE.Vector2(0.38, 1.15),
    new THREE.Vector2(0.44, 1.52),
    new THREE.Vector2(0.22, 1.68),
    new THREE.Vector2(0.01, 1.72),
  ];
  const torsoGeo = new THREE.LatheGeometry(lathePoints, 20);
  const torsoMat = new THREE.MeshStandardMaterial({
    color: 0x0f172a,
    emissive: 0x00f5d4,
    emissiveIntensity: 0.35,
    roughness: 0.25,
    metalness: 0.75,
  });
  const torsoMesh = new THREE.Mesh(torsoGeo, torsoMat);
  avatarBodyGroup.add(torsoMesh);

  // Head & Cyber Visor
  const headMesh = new THREE.Mesh(
    new THREE.SphereGeometry(0.27, 20, 20),
    new THREE.MeshStandardMaterial({ color: 0xe2e8f0, roughness: 0.3, metalness: 0.4 })
  );
  headMesh.position.y = 1.98;
  avatarBodyGroup.add(headMesh);

  const visorMesh = new THREE.Mesh(
    new THREE.BoxGeometry(0.38, 0.12, 0.22),
    new THREE.MeshBasicMaterial({ color: 0x00f5d4 })
  );
  visorMesh.position.set(0, 1.99, 0.18);
  avatarBodyGroup.add(visorMesh);

  // Legs for walking animation
  const legGeo = new THREE.CylinderGeometry(0.11, 0.09, 0.68, 12);
  const legMat = new THREE.MeshStandardMaterial({ color: 0x1e293b, roughness: 0.5 });
  const leftLeg = new THREE.Mesh(legGeo, legMat);
  leftLeg.position.set(-0.16, 0.34, 0);
  avatarBodyGroup.add(leftLeg);

  const rightLeg = new THREE.Mesh(legGeo, legMat);
  rightLeg.position.set(0.16, 0.34, 0);
  avatarBodyGroup.add(rightLeg);

  // Ground Aura Ring under Player
  const auraRingGeo = new THREE.PlaneGeometry(4.8, 4.8);
  auraRingGeo.rotateX(-Math.PI / 2);
  const auraRingMat = new THREE.MeshBasicMaterial({
    map: createAuraFloorTexture(state.auraColor),
    transparent: true,
    depthWrite: false,
    side: THREE.DoubleSide,
  });
  const auraRingMesh = new THREE.Mesh(auraRingGeo, auraRingMat);
  auraRingMesh.position.y = 0.09;
  playerGroup.add(auraRingMesh);

  // Vertical Energy Cylinder around Player
  const auraCylGeo = new THREE.CylinderGeometry(1.65, 1.65, 1.4, 32, 1, true);
  const auraCylMat = new THREE.MeshBasicMaterial({
    color: 0x00f5d4,
    transparent: true,
    opacity: 0.16,
    side: THREE.DoubleSide,
    depthWrite: false,
  });
  const auraCylMesh = new THREE.Mesh(auraCylGeo, auraCylMat);
  auraCylMesh.position.y = 0.75;
  playerGroup.add(auraCylMesh);

  // Floating 3D Aura Text Sprite above Player
  const playerBannerMat = new THREE.SpriteMaterial({
    map: createTextBannerTexture(state.auraText, "АУРА ИГРОКА · ДНЕПР 3D", state.auraColor, true),
    transparent: true,
  });
  const playerBannerSprite = new THREE.Sprite(playerBannerMat);
  playerBannerSprite.position.set(0, 3.15, 0);
  playerBannerSprite.scale.set(4.8, 1.2, 1);
  playerGroup.add(playerBannerSprite);

  // Build 3D GTA Sportscar (Parked at European Square / TsUM)
  const carGroup = new THREE.Group();
  scene.add(carGroup);

  const carBodyMat = new THREE.MeshStandardMaterial({
    color: 0xe11d48,
    emissive: 0x4c0519,
    roughness: 0.18,
    metalness: 0.85,
  });
  const carChassis = new THREE.Mesh(new THREE.BoxGeometry(2.2, 0.68, 4.6), carBodyMat);
  carChassis.position.y = 0.62;
  carGroup.add(carChassis);

  const carCabin = new THREE.Mesh(
    new THREE.BoxGeometry(1.85, 0.55, 2.3),
    new THREE.MeshStandardMaterial({ color: 0x090d16, roughness: 0.1, metalness: 0.9 })
  );
  carCabin.position.set(0, 1.15, -0.2);
  carGroup.add(carCabin);

  // Neon Underglow on GTA Car
  const carUnderglowMat = new THREE.MeshBasicMaterial({
    color: 0x00f5d4,
    transparent: true,
    opacity: 0.55,
    side: THREE.DoubleSide,
  });
  const carUnderglow = new THREE.Mesh(new THREE.PlaneGeometry(2.8, 5.2).rotateX(-Math.PI / 2), carUnderglowMat);
  carUnderglow.position.y = 0.08;
  carGroup.add(carUnderglow);

  // Headlights & Taillights & 4 Wheels + GT Spoiler
  const hlGeo = new THREE.BoxGeometry(0.45, 0.16, 0.12);
  const hlMat = new THREE.MeshBasicMaterial({ color: 0xfef08a });
  const hlLeft = new THREE.Mesh(hlGeo, hlMat);
  hlLeft.position.set(-0.72, 0.68, 2.31);
  const hlRight = new THREE.Mesh(hlGeo, hlMat);
  hlRight.position.set(0.72, 0.68, 2.31);
  carGroup.add(hlLeft, hlRight);

  const tlMat = new THREE.MeshBasicMaterial({ color: 0xff1744 });
  const tlBar = new THREE.Mesh(new THREE.BoxGeometry(1.95, 0.14, 0.1), tlMat);
  tlBar.position.set(0, 0.76, -2.31);
  carGroup.add(tlBar);

  // Rear GT Spoiler Wing
  const wingDeck = new THREE.Mesh(new THREE.BoxGeometry(2.15, 0.08, 0.42), carBodyMat);
  wingDeck.position.set(0, 1.18, -2.05);
  carGroup.add(wingDeck);

  // 4 Sport Wheels with Neon Rims
  const wheelGeo = new THREE.CylinderGeometry(0.42, 0.42, 0.32, 18);
  wheelGeo.rotateZ(Math.PI / 2);
  const wheelMat = new THREE.MeshStandardMaterial({ color: 0x0f172a, roughness: 0.6 });
  const rimGeo = new THREE.CylinderGeometry(0.26, 0.26, 0.34, 14);
  rimGeo.rotateZ(Math.PI / 2);
  const rimMat = new THREE.MeshBasicMaterial({ color: 0x00f5d4 });
  [
    [-1.12, 0.42, 1.45],
    [1.12, 0.42, 1.45],
    [-1.12, 0.42, -1.45],
    [1.12, 0.42, -1.45],
  ].forEach(function (pos) {
    const wMesh = new THREE.Mesh(wheelGeo, wheelMat);
    wMesh.position.set(pos[0], pos[1], pos[2]);
    const rMesh = new THREE.Mesh(rimGeo, rimMat);
    rMesh.position.set(pos[0], pos[1], pos[2]);
    carGroup.add(wMesh, rMesh);
  });

  // Car Roof Floating Sign
  const carSignMat = new THREE.SpriteMaterial({
    map: createTextBannerTexture("🚗 GTA SPORT · ЦУМ", "НАЖМИ «В ТАЧКУ»", "#facc15", true),
    transparent: true,
  });
  const carSignSprite = new THREE.Sprite(carSignMat);
  carSignSprite.position.set(0, 2.45, 0);
  carSignSprite.scale.set(3.8, 0.95, 1);
  carGroup.add(carSignSprite);

  // Checkpoints Visual Rings (GTA Mission Markers)
  const checkpointMeshes = [];
  state.checkpoints.forEach(function (cp) {
    const cpGroup = new THREE.Group();
    cpGroup.position.set(cp.x, 0, cp.z);

    const cyl = new THREE.Mesh(
      new THREE.CylinderGeometry(2.1, 2.1, 1.8, 24, 1, true),
      new THREE.MeshBasicMaterial({
        color: 0xfacc15,
        transparent: true,
        opacity: 0.22,
        side: THREE.DoubleSide,
        depthWrite: false,
      })
    );
    cyl.position.y = 0.9;
    cpGroup.add(cyl);

    const labelSprite = new THREE.Sprite(
      new THREE.SpriteMaterial({
        map: createTextBannerTexture("🎯 " + cp.title, "ЧЕКПОИНТ +$50,000", "#facc15", true),
        transparent: true,
      })
    );
    labelSprite.position.y = 6.5;
    labelSprite.scale.set(5.2, 1.3, 1);
    cpGroup.add(labelSprite);

    scene.add(cpGroup);
    checkpointMeshes.push({ cp, group: cpGroup, cyl });
  });

  // Load Real Dnipro OSM Buildings & Roads
  const cityGroup = new THREE.Group();
  scene.add(cityGroup);

  function buildDniproCity(geojson) {
    const features = (geojson && geojson.features) || [];
    features.forEach(function (feat) {
      const props = feat.properties || {};
      const geom = feat.geometry || {};

      if (props.kind === "building" && geom.type === "Polygon" && geom.coordinates && geom.coordinates[0]) {
        const ring = geom.coordinates[0];
        const pts2d = [];
        let sumX = 0;
        let sumZ = 0;

        ring.forEach(function (coord) {
          const p = gpsToWorld(coord[1], coord[0]);
          // Shape is in XY plane; when rotated -PI/2 around X, shape.y becomes -Z. So pass (p.x, -p.z)
          pts2d.push(new THREE.Vector2(p.x, -p.z));
          sumX += p.x;
          sumZ += p.z;
        });

        if (pts2d.length < 3) return;
        const cx = sumX / pts2d.length;
        const cz = sumZ / pts2d.length;

        const name = props.name || "";
        const levels = parseFloat(props.levels) || (name ? 5 : 3.5);
        const height = Math.max(8, Math.min(38, parseFloat(props.height) || levels * 3.8));

        const shape = new THREE.Shape(pts2d);
        const extrudeGeo = new THREE.ExtrudeGeometry(shape, {
          depth: height,
          bevelEnabled: true,
          bevelThickness: 0.3,
          bevelSize: 0.2,
          bevelSegments: 1,
        });
        extrudeGeo.rotateX(-Math.PI / 2);

        const bMat = new THREE.MeshStandardMaterial({
          map: sharedFacadeTex,
          color: name ? 0xffffff : 0xcbd5e1,
          roughness: 0.45,
          metalness: 0.3,
        });

        const mesh = new THREE.Mesh(extrudeGeo, bMat);
        mesh.castShadow = true;
        mesh.receiveShadow = true;
        cityGroup.add(mesh);

        state.buildings.push({ name, cx, cz, height });
        state.buildingMeshes.push(mesh);

        // Add Rooftop Neon Hologram Billboards on Named Dnipro Landmarks
        if (name) {
          const isMajor =
            name.includes("ЦУМ") ||
            name.includes("Європа") ||
            name.includes("Ротонда") ||
            name.includes("Скорпіон") ||
            name.includes("карусель") ||
            name.includes("Mafia");

          const sub = isMajor ? "ТЕСТ АУРЫ: " + state.auraText : "ДНЕПР · " + Math.round(height) + "М";
          const spriteMat = new THREE.SpriteMaterial({
            map: createTextBannerTexture(name, sub, isMajor ? state.auraColor : "#38bdf8", false),
            transparent: true,
          });
          const sprite = new THREE.Sprite(spriteMat);
          sprite.position.set(cx, height + (isMajor ? 5.2 : 3.4), cz);
          const scaleW = isMajor ? 13.5 : 8.5;
          sprite.scale.set(scaleW, scaleW * 0.25, 1);
          scene.add(sprite);

          if (isMajor) {
            state.billboardSprites.push({ sprite, landmarkName: name });
          }
        }
      } else if (props.kind === "road" && geom.type === "LineString" && geom.coordinates) {
        const roadPts = geom.coordinates.map(function (c) {
          const p = gpsToWorld(c[1], c[0]);
          return new THREE.Vector3(p.x, 0.14, p.z);
        });
        if (roadPts.length >= 2) {
          state.roads.push({ name: props.name || "Улица Днепра", highway: props.highway, points: roadPts });
          const lineGeo = new THREE.BufferGeometry().setFromPoints(roadPts);
          const isMain = (props.name || "").includes("Яворницького") || (props.name || "").includes("Центральна");
          const lineMat = new THREE.LineBasicMaterial({
            color: isMain ? 0x00f5d4 : 0x38bdf8,
            transparent: true,
            opacity: isMain ? 0.65 : 0.32,
          });
          scene.add(new THREE.Line(lineGeo, lineMat));
        }
      }
    });
  }

  fetch("dnipro_assets/osm_buildings_roads.geojson")
    .then(function (r) { return r.json(); })
    .then(buildDniproCity)
    .catch(function () {});

  // Load 52 Real Dnipro Street View Panoramas & Create Glowing Street Portals
  const streetPortalsGroup = new THREE.Group();
  scene.add(streetPortalsGroup);

  fetch("dnipro_assets/streetview/photos.geojson")
    .then(function (r) { return r.json(); })
    .then(function (gj) {
      const feats = (gj && gj.features) || [];
      feats.forEach(function (f, idx) {
        const coords = (f.geometry && f.geometry.coordinates) || [BOUNDS.centerLon, BOUNDS.centerLat];
        const props = f.properties || {};
        const pos = gpsToWorld(coords[1], coords[0]);
        const relFile = props.filename || props.file || "streetview/photos/000001_path_p0001_s0001_left_h218.jpg";
        const rawRoad = String(props.path_name || "");
        const cleanRoad =
          !rawRoad || rawRoad.indexOf("\ufffd") !== -1
            ? "пр. Дмитра Яворницького · Центр Днепра"
            : rawRoad;
        const item = {
          idx,
          x: pos.x,
          z: pos.z,
          file: "dnipro_assets/" + relFile,
          roadName: cleanRoad,
          heading: props.heading || 0,
          date: props.imagery_date || props.date || "2026",
        };
        state.streetPhotos.push(item);

        // Place glowing cyan photo beacon every 2nd panorama
        if (idx % 2 === 0) {
          const orb = new THREE.Mesh(
            new THREE.OctahedronGeometry(0.65, 0),
            new THREE.MeshBasicMaterial({ color: 0x38bdf8, wireframe: true })
          );
          orb.position.set(pos.x, 1.6, pos.z);
          streetPortalsGroup.add(orb);
        }
      });
    })
    .catch(function () {});

  // Update All Aura Text & Texture Shaders Live!
  function refreshAuraAndTextures(customPhotoImg) {
    const col = new THREE.Color(state.auraColor);
    neonPointLight.color.copy(col);
    visorMesh.material.color.copy(col);
    torsoMat.emissive.copy(col);
    auraCylMat.color.copy(col);
    carUnderglowMat.color.copy(col);

    // Update Player Floating Banner
    if (playerBannerMat.map) playerBannerMat.map.dispose();
    playerBannerMat.map = createTextBannerTexture(
      state.auraText,
      "ТЕКСТ АУРЫ · " + state.textureMode.toUpperCase(),
      state.auraColor,
      true
    );
    playerBannerMat.needsUpdate = true;

    // Update Player Floor Ring with Circular Text
    if (auraRingMat.map) auraRingMat.map.dispose();
    auraRingMat.map = createAuraFloorTexture(state.auraColor);
    auraRingMat.needsUpdate = true;

    // Update Dnipro Landmark Rooftop Hologram Billboards (ЦУМ, Європа, Ротонда...)
    state.billboardSprites.forEach(function (entry) {
      if (entry.sprite.material.map) entry.sprite.material.map.dispose();
      entry.sprite.material.map = createTextBannerTexture(
        entry.landmarkName + " · " + state.auraText,
        "ДНЕПР 3D БИЛБОРД · BOSSMAN",
        state.auraColor,
        false
      );
      entry.sprite.material.needsUpdate = true;
    });

    // Update City Building Materials
    sharedFacadeTex = makeFacadeTexture(state.textureMode, state.auraColor, customPhotoImg || null);
    state.buildingMeshes.forEach(function (m) {
      m.material.wireframe = state.textureMode === "wireframe";
      m.material.map = state.textureMode === "wireframe" ? null : sharedFacadeTex;
      if (state.textureMode === "neon") {
        m.material.emissive = col;
        m.material.emissiveIntensity = 0.22;
      } else if (state.textureMode === "gold") {
        m.material.emissive = new THREE.Color(0xfacc15);
        m.material.emissiveIntensity = 0.18;
      } else {
        m.material.emissive = new THREE.Color(0x000000);
        m.material.emissiveIntensity = 0;
      }
      m.material.needsUpdate = true;
    });
  }

  function showBanner(title, sub) {
    gtaBannerTitle.textContent = title;
    gtaBannerSub.textContent = sub;
    gtaBanner.classList.remove("hidden");
    clearTimeout(showBanner._t);
    showBanner._t = setTimeout(function () {
      gtaBanner.classList.add("hidden");
    }, 2600);
  }

  // Load Street View Photo into Modal & Optionally Project onto 3D Buildings
  function showStreetPhoto(idx, openModal) {
    if (!state.streetPhotos.length) return;
    state.currentPhotoIdx = (idx + state.streetPhotos.length) % state.streetPhotos.length;
    const item = state.streetPhotos[state.currentPhotoIdx];
    svTitle.textContent = "📸 " + item.roadName;
    svMeta.textContent =
      "Панорама #" + (state.currentPhotoIdx + 1) + " из " + state.streetPhotos.length + " · Курс " + Math.round(item.heading) + "°";
    svImage.src = item.file;
    if (openModal) {
      streetModal.classList.remove("hidden");
    }
  }

  // Toggle Real Photogrammetry 3D Mesh (mesh.glb)
  const btnToggleMesh = document.getElementById("btnToggleMesh");
  btnToggleMesh.addEventListener("click", function () {
    if (!state.meshLoaded) {
      btnToggleMesh.textContent = "⏳ Загрузка GLB...";
      const loader = new THREE.GLTFLoader();
      loader.load(
        "dnipro_assets/mesh.glb",
        function (gltf) {
          state.meshLoaded = true;
          state.meshVisible = true;
          state.meshObject = gltf.scene;
          const box = new THREE.Box3().setFromObject(state.meshObject);
          const center = box.getCenter(new THREE.Vector3());
          const size = box.getSize(new THREE.Vector3());
          const maxDim = Math.max(size.x, size.y, size.z) || 1;
          const scale = BOUNDS.widthM / maxDim;
          state.meshObject.scale.setScalar(scale);
          state.meshObject.position.set(-center.x * scale, 1.5, -center.z * scale);
          scene.add(state.meshObject);
          btnToggleMesh.classList.add("active");
          btnToggleMesh.textContent = "🧱 3D Меш: ВКЛ";
          showBanner("3D MESH LOADED", "Фотограмметрия центра Днепра (mesh.glb) подключена");
        },
        undefined,
        function () {
          btnToggleMesh.textContent = "🧱 3D Меш";
        }
      );
    } else if (state.meshObject) {
      state.meshVisible = !state.meshVisible;
      state.meshObject.visible = state.meshVisible;
      btnToggleMesh.classList.toggle("active", state.meshVisible);
      btnToggleMesh.textContent = state.meshVisible ? "🧱 3D Меш: ВКЛ" : "🧱 3D Меш";
    }
  });

  // UI Event Bindings
  document.getElementById("btnToggleAuraDrawer").addEventListener("click", function () {
    const isHidden = auraDrawer.classList.toggle("hidden");
    this.classList.toggle("active", !isHidden);
  });
  document.getElementById("btnCloseAuraDrawer").addEventListener("click", function () {
    auraDrawer.classList.add("hidden");
    document.getElementById("btnToggleAuraDrawer").classList.remove("active");
  });

  function applyAuraTextFromInput() {
    const val = (auraTextInput.value || "").trim() || "BOSSMAN · ДНЕПР 2026";
    state.auraText = val;
    refreshAuraAndTextures(null);
    showBanner("АУРА ОБНОВЛЕНА", val);
  }

  document.getElementById("btnApplyAuraText").addEventListener("click", applyAuraTextFromInput);
  auraTextInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter") {
      e.preventDefault();
      applyAuraTextFromInput();
      auraTextInput.blur();
    }
    e.stopPropagation();
  });
  auraTextInput.addEventListener("input", function () {
    state.auraText = (auraTextInput.value || "").trim() || "BOSSMAN · ДНЕПР 2026";
    refreshAuraAndTextures(null);
  });

  document.getElementById("auraTextPresets").addEventListener("click", function (e) {
    const btn = e.target.closest("[data-text]");
    if (!btn) return;
    const t = btn.getAttribute("data-text");
    auraTextInput.value = t;
    state.auraText = t;
    refreshAuraAndTextures(null);
    showBanner("ПРЕСЕТ АУРЫ", t);
  });

  document.getElementById("textureModeGrid").addEventListener("click", function (e) {
    const btn = e.target.closest("[data-mode]");
    if (!btn) return;
    document.querySelectorAll("#textureModeGrid .tex-btn").forEach(function (b) {
      b.classList.remove("active");
    });
    btn.classList.add("active");
    state.textureMode = btn.getAttribute("data-mode");
    if (state.textureMode === "streetproj") {
      const img = new Image();
      img.onload = function () {
        refreshAuraAndTextures(img);
        showBanner("ФАСАДЫ STREET VIEW", "Реальное фото Днепра натянуто на 3D-здания");
      };
      img.src = svImage.src;
    } else {
      refreshAuraAndTextures(null);
      showBanner("ТЕКСТУРЫ ОБНОВЛЕНЫ", btn.textContent.trim());
    }
  });

  document.getElementById("auraColorRow").addEventListener("click", function (e) {
    const sw = e.target.closest("[data-color]");
    if (!sw) return;
    document.querySelectorAll("#auraColorRow .color-swatch").forEach(function (b) {
      b.classList.remove("active");
    });
    sw.classList.add("active");
    state.auraColor = sw.getAttribute("data-color");
    refreshAuraAndTextures(null);
  });

  document.getElementById("teleportRow").addEventListener("click", function (e) {
    const btn = e.target.closest("[data-tp]");
    if (!btn) return;
    const lm = LANDMARKS[btn.getAttribute("data-tp")];
    if (!lm) return;
    if (state.mode === "drive") {
      state.car.x = lm.x;
      state.car.z = lm.z;
      state.car.speed = 0;
    } else {
      state.player.x = lm.x;
      state.player.z = lm.z;
    }
    hudStreetName.textContent = lm.street;
    showBanner("ТЕЛЕПОРТ · ДНЕПР", lm.name);
  });

  // Street View Modal Controls
  document.getElementById("btnToggleStreetModal").addEventListener("click", function () {
    const hidden = streetModal.classList.toggle("hidden");
    this.classList.toggle("active", !hidden);
  });
  document.getElementById("btnCloseStreetModal").addEventListener("click", function () {
    streetModal.classList.add("hidden");
    document.getElementById("btnToggleStreetModal").classList.remove("active");
  });
  document.getElementById("btnPrevPhoto").addEventListener("click", function () {
    showStreetPhoto(state.currentPhotoIdx - 1, true);
  });
  document.getElementById("btnNextPhoto").addEventListener("click", function () {
    showStreetPhoto(state.currentPhotoIdx + 1, true);
  });
  document.getElementById("btnApplyPhotoTexture").addEventListener("click", function () {
    const img = new Image();
    img.onload = function () {
      state.textureMode = "streetproj";
      refreshAuraAndTextures(img);
      showBanner("ТЕКСТУРА УЛИЦЫ АКТИВНА", "Панорама Днепра применена к 3D зданиям");
    };
    img.src = svImage.src;
  });

  // GTA Action Pad Buttons
  const btnDriveToggle = document.getElementById("btnDriveToggle");
  const driveBtnIcon = document.getElementById("driveBtnIcon");
  const driveBtnText = document.getElementById("driveBtnText");

  function toggleDriveMode() {
    if (state.mode === "walk") {
      state.mode = "drive";
      state.car.x = state.player.x;
      state.car.z = state.player.z;
      state.car.yaw = state.player.yaw;
      playerGroup.visible = false;
      carSignSprite.material.map = createTextBannerTexture(state.auraText, "GTA SPORT · ДНЕПР", state.auraColor, true);
      driveBtnIcon.textContent = "🚶";
      driveBtnText.textContent = "Пешком";
      hudModeLabel.textContent = "🚗 Тачка GTA";
      showBanner("GTA DRIVE MODE", "Спорткар активирован · Гони по пр. Яворницького!");
    } else {
      state.mode = "walk";
      state.player.x = state.car.x + 2.4;
      state.player.z = state.car.z;
      state.player.yaw = state.car.yaw;
      playerGroup.visible = true;
      driveBtnIcon.textContent = "🚗";
      driveBtnText.textContent = "В тачку";
      hudModeLabel.textContent = "🚶 Пешком";
      showBanner("ПЕШИЙ РЕЖИМ", "Тест текста ауры и текстур вокруг персонажа");
    }
  }
  btnDriveToggle.addEventListener("click", toggleDriveMode);

  document.getElementById("btnBoost").addEventListener("click", function () {
    state.player.boostTimer = 2.5;
    showBanner("⚡ НИТРО ИМПУЛЬС", "Ускорение +200% по центру Днепра");
  });

  document.getElementById("btnPulseAura").addEventListener("click", function () {
    state.player.pulseScale = 2.6;
    state.cash += 15000;
    hudCash.textContent = "$BOSS " + state.cash.toLocaleString("en-US");
    showBanner("💥 ИМПУЛЬС АУРЫ", state.auraText + " · +$BOSS 15,000");
  });

  document.getElementById("btnNearestStreetView").addEventListener("click", function () {
    const px = state.mode === "drive" ? state.car.x : state.player.x;
    const pz = state.mode === "drive" ? state.car.z : state.player.z;
    let bestIdx = 0;
    let bestDist = Infinity;
    state.streetPhotos.forEach(function (sp, idx) {
      const d = Math.hypot(sp.x - px, sp.z - pz);
      if (d < bestDist) {
        bestDist = d;
        bestIdx = idx;
      }
    });
    showStreetPhoto(bestIdx, true);
  });

  document.getElementById("btnCameraView").addEventListener("click", function () {
    state.cameraPreset = (state.cameraPreset + 1) % 3;
    const labels = ["🎥 Камера: GTA", "🎥 Камера: Аура Вблизи", "🎥 Камера: Дрон 3D"];
    this.textContent = labels[state.cameraPreset];
  });

  document.getElementById("radarWrap").addEventListener("click", function () {
    state.cameraPreset = state.cameraPreset === 2 ? 0 : 2;
  });

  // Touch Virtual Joystick (Canvas-rendered, setPointerCapture, 0 inline styles)
  function drawJoystick() {
    const w = joystickCanvas.width;
    const h = joystickCanvas.height;
    const cx = w / 2;
    const cy = h / 2;
    joyCtx.clearRect(0, 0, w, h);

    // Outer Ring
    joyCtx.beginPath();
    joyCtx.arc(cx, cy, 56, 0, Math.PI * 2);
    joyCtx.fillStyle = "rgba(10, 18, 34, 0.72)";
    joyCtx.fill();
    joyCtx.lineWidth = 2.5;
    joyCtx.strokeStyle = state.auraColor;
    joyCtx.stroke();

    // Crosshair ticks
    joyCtx.strokeStyle = "rgba(148, 163, 184, 0.25)";
    joyCtx.lineWidth = 1.5;
    joyCtx.beginPath();
    joyCtx.moveTo(cx - 42, cy);
    joyCtx.lineTo(cx + 42, cy);
    joyCtx.moveTo(cx, cy - 42);
    joyCtx.lineTo(cx, cy + 42);
    joyCtx.stroke();

    // Thumb Knob
    const kx = cx + state.joy.dx * 42;
    const ky = cy + state.joy.dy * 42;
    joyCtx.beginPath();
    joyCtx.arc(kx, ky, 22, 0, Math.PI * 2);
    joyCtx.fillStyle = state.auraColor;
    joyCtx.fill();
    joyCtx.lineWidth = 3;
    joyCtx.strokeStyle = "#ffffff";
    joyCtx.stroke();
  }
  drawJoystick();

  function updateJoystickFromEvent(e) {
    const rect = joystickCanvas.getBoundingClientRect();
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    const maxR = rect.width * 0.36;
    let dx = (e.clientX - cx) / maxR;
    let dy = (e.clientY - cy) / maxR;
    const mag = Math.hypot(dx, dy);
    if (mag > 1) {
      dx /= mag;
      dy /= mag;
    }
    state.joy.dx = dx;
    state.joy.dy = dy;
    drawJoystick();
  }

  joystickCanvas.addEventListener("pointerdown", function (e) {
    state.joy.active = true;
    state.joy.pointerId = e.pointerId;
    if (joystickCanvas.setPointerCapture) {
      joystickCanvas.setPointerCapture(e.pointerId);
    }
    updateJoystickFromEvent(e);
  });
  joystickCanvas.addEventListener("pointermove", function (e) {
    if (!state.joy.active || e.pointerId !== state.joy.pointerId) return;
    updateJoystickFromEvent(e);
  });
  function releaseJoystick(e) {
    if (e && state.joy.pointerId !== null && e.pointerId !== state.joy.pointerId) return;
    state.joy.active = false;
    state.joy.pointerId = null;
    state.joy.dx = 0;
    state.joy.dy = 0;
    drawJoystick();
  }
  joystickCanvas.addEventListener("pointerup", releaseJoystick);
  joystickCanvas.addEventListener("pointercancel", releaseJoystick);

  // Touch Camera Orbit on World Canvas
  let camTouch = { active: false, id: null, lastX: 0, lastY: 0 };
  worldCanvas.addEventListener("pointerdown", function (e) {
    camTouch.active = true;
    camTouch.id = e.pointerId;
    camTouch.lastX = e.clientX;
    camTouch.lastY = e.clientY;
  });
  worldCanvas.addEventListener("pointermove", function (e) {
    if (!camTouch.active || e.pointerId !== camTouch.id) return;
    const dx = e.clientX - camTouch.lastX;
    const dy = e.clientY - camTouch.lastY;
    camTouch.lastX = e.clientX;
    camTouch.lastY = e.clientY;
    state.cameraYaw -= dx * 0.007;
    state.cameraPitch = Math.max(0.18, Math.min(1.25, state.cameraPitch + dy * 0.005));
  });
  window.addEventListener("pointerup", function (e) {
    if (e.pointerId === camTouch.id) camTouch.active = false;
  });

  // Keyboard Controls (WASD / Arrows / Space / E)
  window.addEventListener("keydown", function (e) {
    if (document.activeElement === auraTextInput) return;
    state.keys[e.code] = true;
    if (e.code === "KeyF" || e.code === "KeyE") toggleDriveMode();
    if (e.code === "Space") state.player.boostTimer = 1.8;
  });
  window.addEventListener("keyup", function (e) {
    state.keys[e.code] = false;
  });

  // Draw GTA Circular Radar Minimap
  function renderRadar(px, pz, pyaw) {
    const w = radarCanvas.width;
    const h = radarCanvas.height;
    const cx = w / 2;
    const cy = h / 2;
    radarCtx.clearRect(0, 0, w, h);

    radarCtx.save();
    radarCtx.translate(cx, cy);
    radarCtx.rotate(pyaw);

    const scale = 1.15;
    if (state.satelliteImg) {
      radarCtx.drawImage(
        state.satelliteImg,
        (-BOUNDS.widthM / 2 - px) * scale,
        (-BOUNDS.heightM / 2 - pz) * scale,
        BOUNDS.widthM * scale,
        BOUNDS.heightM * scale
      );
      radarCtx.fillStyle = "rgba(5, 10, 22, 0.35)";
      radarCtx.fillRect(-w, -h, w * 2, h * 2);
    }

    // Draw OSM Roads on Radar
    radarCtx.strokeStyle = "#38bdf8";
    radarCtx.lineWidth = 2;
    state.roads.forEach(function (r) {
      radarCtx.beginPath();
      r.points.forEach(function (pt, idx) {
        const rx = (pt.x - px) * scale;
        const ry = (pt.z - pz) * scale;
        if (idx === 0) radarCtx.moveTo(rx, ry);
        else radarCtx.lineTo(rx, ry);
      });
      radarCtx.stroke();
    });

    // Draw Checkpoints on Radar
    state.checkpoints.forEach(function (cp) {
      if (cp.done) return;
      const rx = (cp.x - px) * scale;
      const ry = (cp.z - pz) * scale;
      radarCtx.fillStyle = "#facc15";
      radarCtx.beginPath();
      radarCtx.arc(rx, ry, 4.5, 0, Math.PI * 2);
      radarCtx.fill();
    });

    radarCtx.restore();

    // Player Arrow in Center of Radar
    radarCtx.save();
    radarCtx.translate(cx, cy);
    radarCtx.fillStyle = state.auraColor;
    radarCtx.strokeStyle = "#ffffff";
    radarCtx.lineWidth = 2;
    radarCtx.beginPath();
    radarCtx.moveTo(0, -8);
    radarCtx.lineTo(6, 7);
    radarCtx.lineTo(0, 4);
    radarCtx.lineTo(-6, 7);
    radarCtx.closePath();
    radarCtx.fill();
    radarCtx.stroke();
    radarCtx.restore();
  }

  // Main 60 FPS Game Loop
  let lastTime = performance.now();
  function animate(now) {
    requestAnimationFrame(animate);
    const dt = Math.min(0.05, (now - lastTime) / 1000);
    lastTime = now;

    // Input Vector (Joystick + Keyboard)
    let inX = state.joy.dx;
    let inY = state.joy.dy;
    if (state.keys.KeyW || state.keys.ArrowUp) inY -= 1;
    if (state.keys.KeyS || state.keys.ArrowDown) inY += 1;
    if (state.keys.KeyA || state.keys.ArrowLeft) inX -= 1;
    if (state.keys.KeyD || state.keys.ArrowRight) inX += 1;

    const inputMag = Math.min(1, Math.hypot(inX, inY));
    if (state.player.boostTimer > 0) state.player.boostTimer = Math.max(0, state.player.boostTimer - dt);
    const boostMult = state.player.boostTimer > 0 ? 2.15 : 1.0;

    let activeX = state.player.x;
    let activeZ = state.player.z;
    let activeYaw = state.player.yaw;
    let speedKmh = 0;

    if (state.mode === "walk") {
      const walkMaxSpeed = 11.5 * boostMult;
      if (inputMag > 0.06) {
        const moveAngle = Math.atan2(inX, inY) + state.cameraYaw;
        state.player.yaw = moveAngle;
        state.player.speed = inputMag * walkMaxSpeed;
        state.player.x += Math.sin(moveAngle) * state.player.speed * dt;
        state.player.z += Math.cos(moveAngle) * state.player.speed * dt;
        state.player.walkPhase += dt * state.player.speed * 1.4;
      } else {
        state.player.speed = 0;
      }

      // Clamp to Dnipro Capture Bounds
      state.player.x = Math.max(-112, Math.min(112, state.player.x));
      state.player.z = Math.max(-105, Math.min(105, state.player.z));

      playerGroup.position.set(state.player.x, 0, state.player.z);
      avatarBodyGroup.rotation.y = state.player.yaw;

      // Walking limb swing
      const swing = inputMag > 0.06 ? Math.sin(state.player.walkPhase) * 0.45 : 0;
      leftLeg.rotation.x = swing;
      rightLeg.rotation.x = -swing;

      // Aura Ring Rotation & Pulse
      state.player.pulseScale += (1 - state.player.pulseScale) * dt * 5;
      auraRingMesh.rotation.z -= dt * 0.9;
      auraRingMesh.scale.setScalar(state.player.pulseScale);
      auraCylMesh.scale.set(state.player.pulseScale, 1, state.player.pulseScale);

      activeX = state.player.x;
      activeZ = state.player.z;
      activeYaw = state.player.yaw;
      speedKmh = Math.round(state.player.speed * 3.2);
    } else {
      // GTA Sportscar Physics
      const accel = -inY * 32 * boostMult;
      state.car.speed += accel * dt;
      state.car.speed *= Math.pow(0.96, dt * 60);
      state.car.speed = Math.max(-18, Math.min(46 * boostMult, state.car.speed));

      if (Math.abs(state.car.speed) > 0.5) {
        const dir = state.car.speed >= 0 ? 1 : -1;
        state.car.yaw -= inX * 2.2 * dir * dt;
      }
      state.car.x += Math.sin(state.car.yaw) * state.car.speed * dt;
      state.car.z += Math.cos(state.car.yaw) * state.car.speed * dt;
      state.car.x = Math.max(-112, Math.min(112, state.car.x));
      state.car.z = Math.max(-105, Math.min(105, state.car.z));

      state.cameraYaw = state.car.yaw + Math.PI;
      activeX = state.car.x;
      activeZ = state.car.z;
      activeYaw = state.car.yaw;
      speedKmh = Math.round(Math.abs(state.car.speed) * 3.6);
    }

    carGroup.position.set(state.car.x, 0, state.car.z);
    carGroup.rotation.y = state.car.yaw;
    neonPointLight.position.set(activeX, 5.5, activeZ);

    // Camera Follow Modes
    if (state.cameraPreset === 0) {
      // Standard GTA Third-Person
      const dist = state.mode === "drive" ? 13.5 : 8.8;
      const height = state.mode === "drive" ? 5.8 : 4.6;
      camera.position.set(
        activeX + Math.sin(state.cameraYaw) * dist,
        height + state.cameraPitch * 6,
        activeZ + Math.cos(state.cameraYaw) * dist
      );
      camera.lookAt(activeX, 1.9, activeZ);
    } else if (state.cameraPreset === 1) {
      // Close-up Aura & Texture Inspector
      camera.position.set(
        activeX + Math.sin(state.cameraYaw + 0.4) * 4.6,
        2.3,
        activeZ + Math.cos(state.cameraYaw + 0.4) * 4.6
      );
      camera.lookAt(activeX, 1.85, activeZ);
    } else {
      // Drone Birdseye over Dnipro Center
      camera.position.set(activeX + 18, 68, activeZ + 42);
      camera.lookAt(activeX, 0, activeZ);
    }

    // Check GTA Checkpoints
    let doneCount = 0;
    checkpointMeshes.forEach(function (item) {
      item.cyl.rotation.y += dt * 1.5;
      if (item.cp.done) {
        doneCount++;
        return;
      }
      const d = Math.hypot(item.cp.x - activeX, item.cp.z - activeZ);
      if (d < 5.2) {
        item.cp.done = true;
        item.cyl.material.color.setHex(0x22c55e);
        item.cyl.material.opacity = 0.12;
        state.cash += 50000;
        hudCash.textContent = "$BOSS " + state.cash.toLocaleString("en-US");
        showBanner("MISSION RESPECT +$50,000", "Точка проверена: " + item.cp.title);
      }
    });
    hudMissionText.textContent =
      doneCount === state.checkpoints.length
        ? "🏆 Все точки центра Днепра пройдены! (5/5)"
        : "Миссия: Проверить ауру у ЦУМа и ТЦ Європа (" + doneCount + "/5)";

    // Update Telemetry & Nearest Building / Street
    hudSpeedLabel.textContent = speedKmh + " км/ч";
    const gps = worldToGps(activeX, activeZ);
    hudCoordsLabel.textContent =
      gps.lat.toFixed(4) + "°N, " + gps.lon.toFixed(4) + "°E · " + state.buildings.length + " зданий";

    renderRadar(activeX, activeZ, state.cameraYaw);
    renderer.render(scene, camera);
  }

  window.addEventListener("resize", function () {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
  });

  // Expose Quick Test Helper for Playwright / Bossman QA
  window.__BOSSBLOCK_GTA__ = {
    state: state,
    setAuraText: function (txt) {
      auraTextInput.value = txt;
      state.auraText = txt;
      refreshAuraAndTextures(null);
    },
    setTextureMode: function (mode) {
      state.textureMode = mode;
      refreshAuraAndTextures(null);
    },
    toggleDriveMode: toggleDriveMode,
    teleport: function (key) {
      const lm = LANDMARKS[key];
      if (!lm) return;
      state.player.x = lm.x;
      state.player.z = lm.z;
      state.car.x = lm.x;
      state.car.z = lm.z;
    },
  };

  requestAnimationFrame(animate);
})();
