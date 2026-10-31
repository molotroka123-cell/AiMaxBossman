/* GTA: Днепр 3D · Real 3D Open-World Engine (Rigged Soldier.glb + Ferrari 458 ferrari.glb + Dnipro OSM/Satellite/StreetView) */
(function () {
  "use strict";

  // Ensure GLTFLoader uses THREE.TextureLoader (<img>) instead of ImageBitmapLoader (fetch blob:) for strict CSP
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

  const LANDMARKS = {
    tsum: { name: "ЦУМ & Ротонда", x: -12, z: -10, street: "пр. Дмитра Яворницького, 52 · ЦУМ" },
    europa: { name: "Європейська пл. & Карусель", x: 4, z: 2, street: "Європейська площа · ТЦ Європа" },
    yavornytskogo: { name: "пр. Дмитра Яворницького", x: -34, z: -32, street: "проспект Дмитра Яворницького" },
    scorpion: { name: "ТЦ Скорпіон", x: -38, z: 28, street: "вул. Володимира Мономаха · Скорпіон" },
  };

  const SHOE_PRESETS = [
    { id: "cyan", label: "👟 Air Cyan", color: "#00f5d4", carColor: "#dc2626" },
    { id: "gold", label: "👟 Boss Gold", color: "#facc15", carColor: "#eab308" },
    { id: "vice", label: "👟 Vice Pink", color: "#ff2a85", carColor: "#f8fafc" },
    { id: "stealth", label: "👟 Carbon Sky", color: "#38bdf8", carColor: "#1e293b" },
  ];

  const state = {
    mode: "walk", // "walk" | "drive"
    timeOfDay: "day", // "day" | "sunset"
    auraText: "BOSSMAN · ДНЕПР 2026",
    auraColor: "#00f5d4",
    carColorHex: "#dc2626",
    shoePresetIdx: 0,
    textureMode: "realism",
    cash: 350000,
    cameraPreset: 0, // 0: GTA Third-Person, 1: Sneaker & Aura Close-up, 2: Helicopter View
    cameraYaw: 2.35,
    cameraPitch: 0.16,
    player: {
      x: -7.5,
      z: -3.5,
      yaw: -0.75,
      speed: 0,
      boostTimer: 0,
      stepAccum: 0,
    },
    car: {
      x: -4.8,
      z: -6.8,
      yaw: -0.68,
      speed: 0,
      steer: 0,
      wheels: [],
      bodyMaterials: [],
    },
    joy: { active: false, dx: 0, dy: 0, pointerId: null },
    keys: {},
    buildings: [],
    buildingMeshes: [],
    ledScreenMaterials: [],
    roads: [],
    streetPhotos: [],
    currentPhotoIdx: 0,
    checkpoints: [
      { id: "cp_tsum", title: "ЦУМ Днепр & Ротонда", x: -14, z: -14, done: false },
      { id: "cp_carousel", title: "Французька карусель", x: -4, z: 3, done: false },
      { id: "cp_europa", title: "ТЦ Європа & Пассаж", x: 12, z: 8, done: false },
      { id: "cp_yav", title: "пр. Дмитра Яворницького", x: -36, z: -34, done: false },
      { id: "cp_scorpion", title: "ТЦ Скорпіон", x: -38, z: 26, done: false },
    ],
    satelliteImg: null,
    mixer: null,
    animActions: {},
    activeActionName: "Idle",
    shoeMeshes: [],
    footprints: [],
    traffic: [],
  };

  // DOM References
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
  const hudShoeLabel = document.getElementById("hudShoeLabel");
  const gtaBanner = document.getElementById("gtaBanner");
  const gtaBannerTitle = document.getElementById("gtaBannerTitle");
  const gtaBannerSub = document.getElementById("gtaBannerSub");

  const auraDrawer = document.getElementById("auraDrawer");
  const auraTextInput = document.getElementById("auraTextInput");
  const streetModal = document.getElementById("streetModal");
  const svTitle = document.getElementById("svTitle");
  const svMeta = document.getElementById("svMeta");
  const svImage = document.getElementById("svImage");

  // WebGLRenderer with ACES Filmic Tone Mapping & Shadows
  const renderer = new THREE.WebGLRenderer({
    canvas: worldCanvas,
    antialias: true,
    preserveDrawingBuffer: true,
    powerPreference: "high-performance",
  });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(window.innerWidth, window.innerHeight);
  renderer.outputEncoding = THREE.sRGBEncoding;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.12;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x68a8e8);
  scene.fog = new THREE.FogExp2(0x8ec5fc, 0.0022);

  const camera = new THREE.PerspectiveCamera(52, window.innerWidth / window.innerHeight, 0.3, 800);

  // Realistic GTA V Daylight & Sunset Lighting
  const hemiLight = new THREE.HemisphereLight(0xdbeafe, 0x475569, 0.95);
  scene.add(hemiLight);

  const sunLight = new THREE.DirectionalLight(0xfffbeb, 1.65);
  sunLight.position.set(65, 110, 55);
  sunLight.castShadow = true;
  sunLight.shadow.mapSize.width = 2048;
  sunLight.shadow.mapSize.height = 2048;
  sunLight.shadow.camera.near = 10;
  sunLight.shadow.camera.far = 320;
  const d = 95;
  sunLight.shadow.camera.left = -d;
  sunLight.shadow.camera.right = d;
  sunLight.shadow.camera.top = d;
  sunLight.shadow.camera.bottom = -d;
  sunLight.shadow.bias = -0.0005;
  scene.add(sunLight);

  // Sky Dome Gradient
  const skyCanvas = document.createElement("canvas");
  skyCanvas.width = 2;
  skyCanvas.height = 256;
  const skyCtx = skyCanvas.getContext("2d");
  const skyTex = new THREE.CanvasTexture(skyCanvas);

  function paintSky(mode) {
    const g = skyCtx.createLinearGradient(0, 0, 0, 256);
    if (mode === "sunset") {
      g.addColorStop(0, "#1e1b4b");
      g.addColorStop(0.45, "#7c2d12");
      g.addColorStop(0.78, "#f97316");
      g.addColorStop(1, "#fde047");
      scene.background.setHex(0xc2410c);
      scene.fog.color.setHex(0xfb923c);
      hemiLight.color.setHex(0xfed7aa);
      hemiLight.groundColor.setHex(0x312e81);
      sunLight.color.setHex(0xfdba74);
      sunLight.intensity = 1.85;
      sunLight.position.set(-85, 42, -55);
    } else {
      g.addColorStop(0, "#1d4ed8");
      g.addColorStop(0.48, "#60a5fa");
      g.addColorStop(0.85, "#bae6fd");
      g.addColorStop(1, "#fef9c3");
      scene.background.setHex(0x60a5fa);
      scene.fog.color.setHex(0xbae6fd);
      hemiLight.color.setHex(0xe0f2fe);
      hemiLight.groundColor.setHex(0x475569);
      sunLight.color.setHex(0xfffbeb);
      sunLight.intensity = 1.65;
      sunLight.position.set(65, 110, 55);
    }
    skyCtx.fillStyle = g;
    skyCtx.fillRect(0, 0, 2, 256);
    skyTex.needsUpdate = true;
  }
  paintSky("day");

  const skyDome = new THREE.Mesh(
    new THREE.SphereGeometry(420, 24, 16),
    new THREE.MeshBasicMaterial({ map: skyTex, side: THREE.BackSide, depthWrite: false })
  );
  scene.add(skyDome);

  // Shared GLTF + Draco Loader Setup
  const dracoLoader = new THREE.DRACOLoader();
  dracoLoader.setDecoderPath("vendor/draco/");
  dracoLoader.setDecoderConfig({ type: "js" });
  const gltfLoader = new THREE.GLTFLoader();
  gltfLoader.setDRACOLoader(dracoLoader);
  const texLoader = new THREE.TextureLoader();

  // High-Detail Urban Ground + Satellite Ortho + European Square Cobblestone Plaza
  const groundMat = new THREE.MeshStandardMaterial({
    color: 0xd1d5db,
    roughness: 0.9,
    metalness: 0.05,
  });
  const groundMesh = new THREE.Mesh(
    new THREE.PlaneGeometry(BOUNDS.widthM * 1.4, BOUNDS.heightM * 1.4).rotateX(-Math.PI / 2),
    groundMat
  );
  groundMesh.receiveShadow = true;
  scene.add(groundMesh);

  texLoader.load("dnipro_assets/satellite.png", function (tex) {
    tex.encoding = THREE.sRGBEncoding;
    tex.anisotropy = 4;
    groundMat.map = tex;
    groundMat.color.setHex(0xffffff);
    groundMat.needsUpdate = true;
    state.satelliteImg = tex.image;
  });

  // Create High-Res Procedural Asphalt Road Texture with White Lane Dashes & Sidewalks
  function createAsphaltTexture() {
    const c = document.createElement("canvas");
    c.width = 512;
    c.height = 512;
    const ctx = c.getContext("2d");

    // Sidewalk edges
    ctx.fillStyle = "#94a3b8";
    ctx.fillRect(0, 0, 512, 512);
    // Curb line
    ctx.fillStyle = "#cbd5e1";
    ctx.fillRect(44, 0, 8, 512);
    ctx.fillRect(460, 0, 8, 512);

    // Dark Asphalt Roadway
    ctx.fillStyle = "#1e293b";
    ctx.fillRect(52, 0, 408, 512);

    // Subtle asphalt grain
    ctx.fillStyle = "rgba(255,255,255,0.03)";
    for (let i = 0; i < 600; i++) {
      ctx.fillRect(54 + ((i * 73) % 400), (i * 41) % 512, 3, 3);
    }

    // Double Yellow Center Line
    ctx.fillStyle = "#facc15";
    ctx.fillRect(250, 0, 4, 512);
    ctx.fillRect(258, 0, 4, 512);

    // White Dashed Lane Markings
    ctx.fillStyle = "#f8fafc";
    for (let y = 16; y < 512; y += 64) {
      ctx.fillRect(150, y, 5, 34);
      ctx.fillRect(356, y, 5, 34);
    }

    const t = new THREE.CanvasTexture(c);
    t.wrapS = THREE.RepeatWrapping;
    t.wrapT = THREE.RepeatWrapping;
    t.repeat.set(1, 18);
    t.encoding = THREE.sRGBEncoding;
    return t;
  }

  const asphaltTex = createAsphaltTexture();

  // Main Dmytro Yavornytskyi Avenue 3D Roadway + Tram Tracks + European Square Pedestrian Plaza
  (function buildMainAvenueAndPlaza() {
    // Dmytro Yavornytskyi Ave 3D Road Strip (runs diagonally SW to NE through center)
    const aveGeo = new THREE.PlaneGeometry(22, 260);
    aveGeo.rotateX(-Math.PI / 2);
    const aveMat = new THREE.MeshStandardMaterial({
      map: asphaltTex,
      roughness: 0.78,
      metalness: 0.08,
      polygonOffset: true,
      polygonOffsetFactor: -1,
    });
    const aveMesh = new THREE.Mesh(aveGeo, aveMat);
    aveMesh.position.set(-18, 0.06, -14);
    aveMesh.rotation.y = -0.68;
    aveMesh.receiveShadow = true;
    scene.add(aveMesh);

    // Centralna Street / European Square Pedestrian Cobblestone Boulevard
    const plazaCanvas = document.createElement("canvas");
    plazaCanvas.width = 256;
    plazaCanvas.height = 256;
    const pctx = plazaCanvas.getContext("2d");
    pctx.fillStyle = "#475569";
    pctx.fillRect(0, 0, 256, 256);
    pctx.strokeStyle = "#334155";
    pctx.lineWidth = 2;
    for (let y = 0; y < 256; y += 16) {
      for (let x = 0; x < 256; x += 32) {
        const off = (y / 16) % 2 === 0 ? 0 : 16;
        pctx.strokeRect(x + off, y, 32, 16);
      }
    }
    // Zebra Crosswalk Stripes
    pctx.fillStyle = "#f1f5f9";
    for (let x = 20; x < 236; x += 24) {
      pctx.fillRect(x, 10, 14, 36);
    }
    const plazaTex = new THREE.CanvasTexture(plazaCanvas);
    plazaTex.wrapS = THREE.RepeatWrapping;
    plazaTex.wrapT = THREE.RepeatWrapping;
    plazaTex.repeat.set(6, 14);
    plazaTex.encoding = THREE.sRGBEncoding;

    const plazaMesh = new THREE.Mesh(
      new THREE.PlaneGeometry(24, 160).rotateX(-Math.PI / 2),
      new THREE.MeshStandardMaterial({ map: plazaTex, roughness: 0.85 })
    );
    plazaMesh.position.set(2, 0.05, 8);
    plazaMesh.rotation.y = 0.88;
    plazaMesh.receiveShadow = true;
    scene.add(plazaMesh);

    // Boulevard Trees & Streetlamps along Yavornytskyi Ave and European Square
    const trunkGeo = new THREE.CylinderGeometry(0.18, 0.25, 2.4, 8);
    const trunkMat = new THREE.MeshStandardMaterial({ color: 0x5c4033, roughness: 0.9 });
    const crownGeo = new THREE.DodecahedronGeometry(1.85, 1);
    const crownMat = new THREE.MeshStandardMaterial({ color: 0x15803d, roughness: 0.75 });

    const poleGeo = new THREE.CylinderGeometry(0.08, 0.12, 5.2, 8);
    const poleMat = new THREE.MeshStandardMaterial({ color: 0x1e293b, metalness: 0.8, roughness: 0.3 });
    const lampBulbGeo = new THREE.SphereGeometry(0.24, 10, 10);
    const lampBulbMat = new THREE.MeshBasicMaterial({ color: 0xfef08a });

    for (let i = -80; i <= 80; i += 16) {
      [-11.5, 11.5].forEach(function (side) {
        const angle = -0.68;
        const wx = -18 + Math.cos(angle) * side + Math.sin(angle) * i;
        const wz = -14 - Math.sin(angle) * side + Math.cos(angle) * i;

        const tree = new THREE.Group();
        const tr = new THREE.Mesh(trunkGeo, trunkMat);
        tr.position.y = 1.2;
        tr.castShadow = true;
        const cr = new THREE.Mesh(crownGeo, crownMat);
        cr.position.y = 3.2;
        cr.castShadow = true;
        tree.add(tr, cr);
        tree.position.set(wx, 0, wz);
        scene.add(tree);

        if (i % 32 === 0) {
          const lamp = new THREE.Group();
          const pole = new THREE.Mesh(poleGeo, poleMat);
          pole.position.y = 2.6;
          const bulb = new THREE.Mesh(lampBulbGeo, lampBulbMat);
          bulb.position.y = 5.3;
          lamp.add(pole, bulb);
          lamp.position.set(wx * 0.92, 0, wz * 0.92);
          scene.add(lamp);
        }
      });
    }

    // 3D Landmark: Французька карусель (French Carousel on European Square)
    const carouselGroup = new THREE.Group();
    carouselGroup.position.set(-4, 0, 3);
    const baseCyl = new THREE.Mesh(
      new THREE.CylinderGeometry(4.2, 4.5, 0.45, 24),
      new THREE.MeshStandardMaterial({ color: 0x78350f, metalness: 0.3, roughness: 0.6 })
    );
    baseCyl.position.y = 0.22;
    carouselGroup.add(baseCyl);

    const roofCone = new THREE.Mesh(
      new THREE.ConeGeometry(4.6, 2.4, 24),
      new THREE.MeshStandardMaterial({ color: 0xfacc15, emissive: 0x854d0e, emissiveIntensity: 0.25, metalness: 0.65, roughness: 0.25 })
    );
    roofCone.position.y = 4.4;
    carouselGroup.add(roofCone);

    for (let k = 0; k < 8; k++) {
      const a = (k / 8) * Math.PI * 2;
      const col = new THREE.Mesh(
        new THREE.CylinderGeometry(0.07, 0.07, 3.2, 8),
        new THREE.MeshBasicMaterial({ color: 0xfef08a })
      );
      col.position.set(Math.cos(a) * 3.4, 1.8, Math.sin(a) * 3.4);
      carouselGroup.add(col);
    }
    scene.add(carouselGroup);
    state.carouselGroup = carouselGroup;
  })();

  // Realistic Architectural Building Facade Textures (Classical Stone, Glass Mall, Street View Photo, Cyber Neon, Gold)
  function makeBuildingFacadeTexture(style, mode, accentHex, customImage) {
    const c = document.createElement("canvas");
    c.width = 512;
    c.height = 512;
    const ctx = c.getContext("2d");

    if (customImage) {
      ctx.drawImage(customImage, 0, 0, 512, 512);
      const t = new THREE.CanvasTexture(c);
      t.wrapS = THREE.RepeatWrapping;
      t.wrapT = THREE.RepeatWrapping;
      t.repeat.set(0.055, 0.055);
      t.encoding = THREE.sRGBEncoding;
      return t;
    }

    if (mode === "neon") {
      ctx.fillStyle = "#0f172a";
    } else if (mode === "gold") {
      ctx.fillStyle = "#451a03";
    } else if (style === "glass") {
      // Modern Glass Shopping Mall (ТЦ Європа / Пассаж)
      const g = ctx.createLinearGradient(0, 0, 0, 512);
      g.addColorStop(0, "#1e3a8a");
      g.addColorStop(0.5, "#0284c7");
      g.addColorStop(1, "#0f172a");
      ctx.fillStyle = g;
    } else {
      // Classical Dnipro Stone / Brick Architecture (ЦУМ / Yavornytskyi Ave)
      ctx.fillStyle = style === "warm" ? "#d6c7b2" : "#cbd5e1";
    }
    ctx.fillRect(0, 0, 512, 512);

    // Cornices & Floor Bands
    const floors = 6;
    const cols = 6;
    const fh = 512 / floors;
    const cw = 512 / cols;

    for (let f = 0; f < floors; f++) {
      const y = f * fh;
      ctx.fillStyle = mode === "neon" ? accentHex : "rgba(30, 41, 59, 0.28)";
      ctx.fillRect(0, y, 512, 6);

      for (let col = 0; col < cols; col++) {
        const x = col * cw;
        const winW = cw * 0.58;
        const winH = fh * 0.62;
        const wx = x + (cw - winW) / 2;
        const wy = y + (fh - winH) / 2 + 3;

        // Window frame
        ctx.fillStyle = "#1e293b";
        ctx.fillRect(wx - 3, wy - 3, winW + 6, winH + 6);

        // Window glass with sky/warm reflection
        const isLit = (f * 5 + col * 3) % 4 === 0;
        if (mode === "neon") {
          ctx.fillStyle = isLit ? accentHex : "#090d16";
        } else if (mode === "gold") {
          ctx.fillStyle = isLit ? "#fde047" : "#292524";
        } else if (style === "glass") {
          ctx.fillStyle = isLit ? "#bae6fd" : "#0369a1";
        } else {
          ctx.fillStyle = isLit ? "#fef3c7" : "#334155";
        }
        ctx.fillRect(wx, wy, winW, winH);
      }
    }

    const t = new THREE.CanvasTexture(c);
    t.wrapS = THREE.RepeatWrapping;
    t.wrapT = THREE.RepeatWrapping;
    t.repeat.set(0.055, 0.055);
    t.encoding = THREE.sRGBEncoding;
    return t;
  }

  let texClassicWarm = makeBuildingFacadeTexture("warm", "realism", state.auraColor, null);
  let texClassicStone = makeBuildingFacadeTexture("stone", "realism", state.auraColor, null);
  let texModernGlass = makeBuildingFacadeTexture("glass", "realism", state.auraColor, null);

  // Create Wall-Mounted LED Screen Texture for TsUM & ТЦ Європа (Displays Live Aura Text!)
  function createLedBillboardTexture(buildingTitle, auraText, accentColor) {
    const c = document.createElement("canvas");
    c.width = 1024;
    c.height = 256;
    const ctx = c.getContext("2d");

    ctx.fillStyle = "#060b16";
    ctx.fillRect(0, 0, 1024, 256);
    ctx.strokeStyle = accentColor;
    ctx.lineWidth = 10;
    ctx.strokeRect(8, 8, 1008, 240);

    ctx.fillStyle = accentColor;
    ctx.font = "800 32px -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(buildingTitle.toUpperCase() + " · DNIPRO LED SCREEN", 512, 62);

    ctx.shadowColor = accentColor;
    ctx.shadowBlur = 18;
    ctx.fillStyle = "#ffffff";
    ctx.font = "900 68px -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";
    ctx.fillText(auraText.toUpperCase(), 512, 148);
    ctx.shadowBlur = 0;

    ctx.fillStyle = "#94a3b8";
    ctx.font = "700 26px ui-monospace, Consolas, monospace";
    ctx.fillText("ТЕСТ АУРЫ И ТЕКСТУР · BOSSMAN MINI APP", 512, 212);

    const t = new THREE.CanvasTexture(c);
    t.encoding = THREE.sRGBEncoding;
    return t;
  }

  // Build All 67 Real Extruded 3D Buildings of Dnipro Center from OSM GeoJSON
  const cityGroup = new THREE.Group();
  scene.add(cityGroup);

  function buildDniproCity(geojson) {
    const features = (geojson && geojson.features) || [];
    features.forEach(function (feat, idx) {
      const props = feat.properties || {};
      const geom = feat.geometry || {};

      if (props.kind === "building" && geom.type === "Polygon" && geom.coordinates && geom.coordinates[0]) {
        const ring = geom.coordinates[0];
        const pts2d = [];
        let sumX = 0;
        let sumZ = 0;

        ring.forEach(function (coord) {
          const p = gpsToWorld(coord[1], coord[0]);
          pts2d.push(new THREE.Vector2(p.x, -p.z));
          sumX += p.x;
          sumZ += p.z;
        });

        if (pts2d.length < 3) return;
        const cx = sumX / pts2d.length;
        const cz = sumZ / pts2d.length;
        const name = props.name || "";
        const levels = parseFloat(props.levels) || (name ? 5 : 4);
        const height = Math.max(10, Math.min(34, parseFloat(props.height) || levels * 3.8));

        const shape = new THREE.Shape(pts2d);
        const extrudeGeo = new THREE.ExtrudeGeometry(shape, {
          depth: height,
          bevelEnabled: true,
          bevelThickness: 0.25,
          bevelSize: 0.2,
          bevelSegments: 1,
        });
        extrudeGeo.rotateX(-Math.PI / 2);

        // Assign realistic facade texture per building type
        const isGlass = name.includes("Європа") || name.includes("Скорпіон") || idx % 4 === 0;
        const style = isGlass ? "glass" : idx % 2 === 0 ? "warm" : "stone";
        const tex = style === "glass" ? texModernGlass : style === "warm" ? texClassicWarm : texClassicStone;

        const wallMat = new THREE.MeshStandardMaterial({
          map: tex,
          roughness: isGlass ? 0.2 : 0.65,
          metalness: isGlass ? 0.55 : 0.1,
        });
        const roofMat = new THREE.MeshStandardMaterial({
          color: 0x334155,
          roughness: 0.85,
        });

        // ExtrudeGeometry uses material index 0 for top/bottom cap and index 1 for side walls
        const mesh = new THREE.Mesh(extrudeGeo, [roofMat, wallMat]);
        mesh.castShadow = true;
        mesh.receiveShadow = true;
        cityGroup.add(mesh);

        state.buildings.push({ name, cx, cz, height, style });
        state.buildingMeshes.push({ mesh, wallMat, style });

        // Add Architectural 3D Rotunda on TsUM & Mounted LED Aura Screens on Major Landmarks
        if (name.includes("ЦУМ") || name.includes("Ротонда") || name.includes("Європа") || name.includes("Скорпіон")) {
          const ledMat = new THREE.MeshBasicMaterial({
            map: createLedBillboardTexture(name, state.auraText, state.auraColor),
            side: THREE.DoubleSide,
          });
          const ledMesh = new THREE.Mesh(new THREE.PlaneGeometry(14, 3.5), ledMat);
          ledMesh.position.set(cx, height + 2.4, cz);
          ledMesh.lookAt(0, height + 2.4, 0);
          scene.add(ledMesh);
          state.ledScreenMaterials.push({ mat: ledMat, name: name });
        }
      } else if (props.kind === "road" && geom.type === "LineString" && geom.coordinates) {
        const roadPts = geom.coordinates.map(function (c) {
          const p = gpsToWorld(c[1], c[0]);
          return new THREE.Vector3(p.x, 0.12, p.z);
        });
        if (roadPts.length >= 2) {
          state.roads.push({ name: props.name || "Улица Днепра", points: roadPts });
        }
      }
    });
  }

  fetch("dnipro_assets/osm_buildings_roads.geojson")
    .then(function (r) { return r.json(); })
    .then(buildDniproCity)
    .catch(function () {});

  // Load 52 Real Dnipro Street View Panoramas
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
        const cleanRoad = !rawRoad || rawRoad.indexOf("\ufffd") !== -1 ? "пр. Дмитра Яворницького · Центр" : rawRoad;
        state.streetPhotos.push({
          idx,
          x: pos.x,
          z: pos.z,
          file: "dnipro_assets/" + relFile,
          roadName: cleanRoad,
          heading: props.heading || 0,
        });
      });
    })
    .catch(function () {});

  // Ground Aura Ring Texture (Sleek Circular Halo with Rotating Aura Text Under Player's Sneakers)
  function createAuraFloorTexture(accentColor, auraText) {
    const c = document.createElement("canvas");
    c.width = 512;
    c.height = 512;
    const ctx = c.getContext("2d");
    const cx = 256;
    const cy = 256;

    const grad = ctx.createRadialGradient(cx, cy, 40, cx, cy, 240);
    grad.addColorStop(0, "rgba(0,0,0,0)");
    grad.addColorStop(0.68, accentColor + "22");
    grad.addColorStop(0.88, accentColor + "cc");
    grad.addColorStop(0.96, "#ffffff");
    grad.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.arc(cx, cy, 244, 0, Math.PI * 2);
    ctx.fill();

    // Circular Aura Text around the floor ring
    ctx.save();
    ctx.translate(cx, cy);
    ctx.fillStyle = "#ffffff";
    ctx.font = "900 22px sans-serif";
    ctx.textAlign = "center";
    const ringStr = (auraText.toUpperCase() + " ★ ").repeat(2);
    for (let i = 0; i < ringStr.length; i++) {
      const a = (i / ringStr.length) * Math.PI * 2;
      ctx.save();
      ctx.rotate(a);
      ctx.fillText(ringStr[i], 0, -192);
      ctx.restore();
    }
    ctx.restore();

    const t = new THREE.CanvasTexture(c);
    t.encoding = THREE.sRGBEncoding;
    return t;
  }

  // Create Compact Overhead Nameplate for Player
  function createPlayerTagTexture(auraText, accentColor) {
    const c = document.createElement("canvas");
    c.width = 512;
    c.height = 96;
    const ctx = c.getContext("2d");

    ctx.fillStyle = "rgba(8, 13, 26, 0.78)";
    ctx.strokeStyle = accentColor;
    ctx.lineWidth = 4;
    ctx.beginPath();
    ctx.roundRect(8, 8, 496, 80, 20);
    ctx.fill();
    ctx.stroke();

    ctx.fillStyle = "#ffffff";
    ctx.font = "900 32px -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(auraText.toUpperCase(), 256, 48);

    const t = new THREE.CanvasTexture(c);
    t.encoding = THREE.sRGBEncoding;
    return t;
  }

  // Build Player Group + Load Real Rigged 3D Human Character (Soldier.glb) + Attach Custom 3D Sneakers ("Обувь")
  const playerGroup = new THREE.Group();
  scene.add(playerGroup);

  const auraRingMat = new THREE.MeshBasicMaterial({
    map: createAuraFloorTexture(state.auraColor, state.auraText),
    transparent: true,
    depthWrite: false,
    side: THREE.DoubleSide,
  });
  const auraRingMesh = new THREE.Mesh(new THREE.PlaneGeometry(2.8, 2.8).rotateX(-Math.PI / 2), auraRingMat);
  auraRingMesh.position.y = 0.08;
  playerGroup.add(auraRingMesh);

  const playerTagMat = new THREE.SpriteMaterial({
    map: createPlayerTagTexture(state.auraText, state.auraColor),
    transparent: true,
  });
  const playerTagSprite = new THREE.Sprite(playerTagMat);
  playerTagSprite.position.set(0, 2.05, 0);
  playerTagSprite.scale.set(1.05, 0.21, 1);
  playerGroup.add(playerTagSprite);

  // Build 3D Sneaker Mesh ("Обувь Боссмана" with glowing neon sole, tongue & swoosh; Mixamo bone space is in cm)
  function createSneakerMesh(accentHex) {
    const shoeGroup = new THREE.Group();
    const soleMat = new THREE.MeshBasicMaterial({ color: accentHex });
    const upperMat = new THREE.MeshStandardMaterial({ color: 0x0f172a, roughness: 0.25, metalness: 0.5 });
    const stripeMat = new THREE.MeshBasicMaterial({ color: 0xffffff });

    const sole = new THREE.Mesh(new THREE.BoxGeometry(13, 5.5, 30), soleMat);
    sole.position.set(0, -4, 6);
    const upper = new THREE.Mesh(new THREE.BoxGeometry(12, 9, 24), upperMat);
    upper.position.set(0, 2, 4);
    const stripe = new THREE.Mesh(new THREE.BoxGeometry(13.2, 2.8, 15), stripeMat);
    stripe.position.set(0, 2, 5);

    shoeGroup.add(sole, upper, stripe);
    state.shoeMeshes.push(sole);
    return shoeGroup;
  }

  const characterHolder = new THREE.Group();
  playerGroup.add(characterHolder);

  gltfLoader.load("vendor/models/Soldier.glb", function (gltf) {
    const model = gltf.scene;
    model.scale.setScalar(1.08);
    // Soldier.glb faces -Z by default; rotate by PI so local +Z is forward
    model.rotation.y = Math.PI;

    model.traverse(function (obj) {
      if (obj.isMesh) {
        obj.castShadow = true;
        obj.receiveShadow = true;
      }
      // Attach custom 3D Sneakers directly onto LeftFoot and RightFoot bones!
      if (obj.isBone && (obj.name === "mixamorig:LeftFoot" || obj.name === "LeftFoot")) {
        const leftShoe = createSneakerMesh(state.auraColor);
        obj.add(leftShoe);
      }
      if (obj.isBone && (obj.name === "mixamorig:RightFoot" || obj.name === "RightFoot")) {
        const rightShoe = createSneakerMesh(state.auraColor);
        obj.add(rightShoe);
      }
    });

    characterHolder.add(model);

    state.mixer = new THREE.AnimationMixer(model);
    gltf.animations.forEach(function (clip) {
      state.animActions[clip.name] = state.mixer.clipAction(clip);
    });
    if (state.animActions.Idle) {
      state.animActions.Idle.play();
      state.activeActionName = "Idle";
    }
  });

  function switchCharacterAnim(nextName) {
    if (!state.mixer || state.activeActionName === nextName) return;
    const prev = state.animActions[state.activeActionName];
    const next = state.animActions[nextName];
    if (!next) return;
    next.reset().fadeIn(0.18).play();
    if (prev) prev.fadeOut(0.18);
    state.activeActionName = nextName;
  }

  // Spawn Neon Footprint on Pavement when Walking/Running
  const footprintGeo = new THREE.PlaneGeometry(0.22, 0.42).rotateX(-Math.PI / 2);
  function spawnFootprint(x, z, yaw) {
    const mat = new THREE.MeshBasicMaterial({
      color: state.auraColor,
      transparent: true,
      opacity: 0.75,
      depthWrite: false,
    });
    const fp = new THREE.Mesh(footprintGeo, mat);
    fp.position.set(x, 0.07, z);
    fp.rotation.y = yaw;
    scene.add(fp);
    state.footprints.push({ mesh: fp, age: 0 });
  }

  // Load Real 3D Ferrari 458 Italia (ferrari.glb) via In-Process DRACOLoader
  const carGroup = new THREE.Group();
  scene.add(carGroup);

  const carUnderglowMat = new THREE.MeshBasicMaterial({
    color: state.auraColor,
    transparent: true,
    opacity: 0.45,
    depthWrite: false,
    side: THREE.DoubleSide,
  });
  const carUnderglow = new THREE.Mesh(new THREE.PlaneGeometry(2.4, 4.8).rotateX(-Math.PI / 2), carUnderglowMat);
  carUnderglow.position.y = 0.07;
  carGroup.add(carUnderglow);

  gltfLoader.load("vendor/models/ferrari.glb", function (gltf) {
    const carModel = gltf.scene;
    // ferrari.glb faces -Z by default; rotate PI so +Z is forward
    carModel.rotation.y = Math.PI;
    carModel.traverse(function (obj) {
      if (obj.isMesh) {
        obj.castShadow = true;
        obj.receiveShadow = true;
        if (obj.material && obj.material.name === "Body_Color") {
          obj.material.color.set(state.carColorHex);
          obj.material.metalness = 0.75;
          obj.material.roughness = 0.18;
          state.car.bodyMaterials.push(obj.material);
        }
        if (obj.material && obj.material.name === "Glass_Gray") {
          obj.material.transparent = true;
          obj.material.opacity = 0.45;
        }
      }
      if (obj.name && obj.name.indexOf("wheel_") === 0) {
        state.car.wheels.push(obj);
      }
    });
    carGroup.add(carModel);
  });

  // Build Moving AI Traffic along Dmytro Yavornytskyi Ave (Dnipro Tram #1 + CesiumMilkTruck.glb + AI Cruiser)
  const tramGroup = new THREE.Group();
  (function buildDniproTramAndTraffic() {
    // Iconic Red & White Dnipro Tram #1 (Вокзал — ЦУМ — ДІІТ)
    const tramBody = new THREE.Mesh(
      new THREE.BoxGeometry(2.5, 2.7, 13.5),
      new THREE.MeshStandardMaterial({ color: 0xdc2626, roughness: 0.35, metalness: 0.3 })
    );
    tramBody.position.y = 1.65;
    tramBody.castShadow = true;
    const tramStripe = new THREE.Mesh(
      new THREE.BoxGeometry(2.54, 0.85, 13.54),
      new THREE.MeshStandardMaterial({ color: 0xf8fafc, roughness: 0.2 })
    );
    tramStripe.position.y = 2.05;
    tramGroup.add(tramBody, tramStripe);
    scene.add(tramGroup);
    state.traffic.push({ group: tramGroup, t: -65, speed: 9.5, laneOffset: -4.2 });

    // Load Real 3D Delivery Truck (CesiumMilkTruck.glb) as AI Traffic
    gltfLoader.load("vendor/models/CesiumMilkTruck.glb", function (gltf) {
      const truck = gltf.scene;
      truck.scale.setScalar(0.95);
      truck.traverse(function (o) {
        if (o.isMesh) {
          o.castShadow = true;
          o.receiveShadow = true;
        }
      });
      const truckWrap = new THREE.Group();
      truckWrap.add(truck);
      scene.add(truckWrap);
      state.traffic.push({ group: truckWrap, t: 35, speed: -11.0, laneOffset: 4.5 });
    });
  })();

  // Sleek GTA Mission Checkpoints (Golden Ground Rings)
  const checkpointMeshes = [];
  state.checkpoints.forEach(function (cp) {
    const cpGroup = new THREE.Group();
    cpGroup.position.set(cp.x, 0, cp.z);

    const ring = new THREE.Mesh(
      new THREE.RingGeometry(1.6, 2.1, 32).rotateX(-Math.PI / 2),
      new THREE.MeshBasicMaterial({ color: 0xfacc15, side: THREE.DoubleSide })
    );
    ring.position.y = 0.1;

    const halo = new THREE.Mesh(
      new THREE.CylinderGeometry(1.9, 1.9, 1.4, 24, 1, true),
      new THREE.MeshBasicMaterial({
        color: 0xfacc15,
        transparent: true,
        opacity: 0.22,
        side: THREE.DoubleSide,
        depthWrite: false,
      })
    );
    halo.position.y = 0.7;

    cpGroup.add(ring, halo);
    scene.add(cpGroup);
    checkpointMeshes.push({ cp, group: cpGroup, halo });
  });

  // Live Update of Aura Text, Sneaker Color, Ferrari Paint, and Building Facade Textures
  function refreshAuraAndTextures(customPhotoImg) {
    const col = new THREE.Color(state.auraColor);
    carUnderglowMat.color.copy(col);

    state.shoeMeshes.forEach(function (s) {
      s.material.color.copy(col);
    });
    state.car.bodyMaterials.forEach(function (m) {
      m.color.set(state.carColorHex);
    });

    if (auraRingMat.map) auraRingMat.map.dispose();
    auraRingMat.map = createAuraFloorTexture(state.auraColor, state.auraText);
    auraRingMat.needsUpdate = true;

    if (playerTagMat.map) playerTagMat.map.dispose();
    playerTagMat.map = createPlayerTagTexture(state.auraText, state.auraColor);
    playerTagMat.needsUpdate = true;

    // Update TsUM & Європа LED Screens
    state.ledScreenMaterials.forEach(function (item) {
      if (item.mat.map) item.mat.map.dispose();
      item.mat.map = createLedBillboardTexture(item.name, state.auraText, state.auraColor);
      item.mat.needsUpdate = true;
    });

    // Update Building Facade Textures
    texClassicWarm = makeBuildingFacadeTexture("warm", state.textureMode, state.auraColor, customPhotoImg || null);
    texClassicStone = makeBuildingFacadeTexture("stone", state.textureMode, state.auraColor, customPhotoImg || null);
    texModernGlass = makeBuildingFacadeTexture("glass", state.textureMode, state.auraColor, customPhotoImg || null);

    state.buildingMeshes.forEach(function (b) {
      b.wallMat.map = b.style === "glass" ? texModernGlass : b.style === "warm" ? texClassicWarm : texClassicStone;
      b.wallMat.needsUpdate = true;
    });
  }

  function showBanner(title, sub) {
    gtaBannerTitle.textContent = title;
    gtaBannerSub.textContent = sub;
    gtaBanner.classList.remove("hidden");
    clearTimeout(showBanner._t);
    showBanner._t = setTimeout(function () {
      gtaBanner.classList.add("hidden");
    }, 2200);
  }

  // UI Handlers
  document.getElementById("btnToggleAuraDrawer").addEventListener("click", function () {
    const hidden = auraDrawer.classList.toggle("hidden");
    this.classList.toggle("active", !hidden);
    streetModal.classList.add("hidden");
  });
  document.getElementById("btnCloseAuraDrawer").addEventListener("click", function () {
    auraDrawer.classList.add("hidden");
    document.getElementById("btnToggleAuraDrawer").classList.remove("active");
  });

  function applyAuraText() {
    const val = (auraTextInput.value || "").trim() || "BOSSMAN · ДНЕПР 2026";
    state.auraText = val;
    refreshAuraAndTextures(null);
    showBanner("АУРА & LED ЦУМа", val);
  }
  document.getElementById("btnApplyAuraText").addEventListener("click", applyAuraText);
  auraTextInput.addEventListener("input", function () {
    state.auraText = (auraTextInput.value || "").trim() || "BOSSMAN · ДНЕПР 2026";
    refreshAuraAndTextures(null);
  });
  auraTextInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter") {
      e.preventDefault();
      applyAuraText();
      auraTextInput.blur();
    }
    e.stopPropagation();
  });

  document.getElementById("auraTextPresets").addEventListener("click", function (e) {
    const btn = e.target.closest("[data-text]");
    if (!btn) return;
    const t = btn.getAttribute("data-text");
    auraTextInput.value = t;
    state.auraText = t;
    refreshAuraAndTextures(null);
  });

  function applyShoePreset(idx) {
    state.shoePresetIdx = (idx + SHOE_PRESETS.length) % SHOE_PRESETS.length;
    const p = SHOE_PRESETS[state.shoePresetIdx];
    state.auraColor = p.color;
    state.carColorHex = p.carColor;
    hudShoeLabel.textContent = p.label;
    refreshAuraAndTextures(null);
    drawJoystick();
    showBanner("ОБУВЬ & FERRARI", p.label + " активированы");
  }

  document.getElementById("shoePresetRow").addEventListener("click", function (e) {
    const btn = e.target.closest("[data-shoe]");
    if (!btn) return;
    document.querySelectorAll("#shoePresetRow .mini-chip").forEach(function (b) {
      b.classList.remove("active");
    });
    btn.classList.add("active");
    state.auraColor = btn.getAttribute("data-color");
    state.carColorHex = btn.getAttribute("data-car");
    hudShoeLabel.textContent = btn.textContent.split("+")[0].trim();
    refreshAuraAndTextures(null);
    drawJoystick();
  });

  document.getElementById("btnShoeSwitch").addEventListener("click", function () {
    applyShoePreset(state.shoePresetIdx + 1);
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
        showBanner("ФАСАДЫ ДНЕПРА", "Реальное фото улицы натянуто на 3D-здания");
      };
      img.src = svImage.src;
    } else {
      refreshAuraAndTextures(null);
    }
  });

  document.getElementById("teleportRow").addEventListener("click", function (e) {
    const btn = e.target.closest("[data-tp]");
    if (!btn) return;
    const lm = LANDMARKS[btn.getAttribute("data-tp")];
    if (!lm) return;
    state.player.x = lm.x;
    state.player.z = lm.z;
    state.car.x = lm.x + 3.2;
    state.car.z = lm.z - 2.5;
    hudStreetName.textContent = lm.street;
    showBanner("ТЕЛЕПОРТ · ДНЕПР", lm.name);
  });

  document.getElementById("btnTimeOfDay").addEventListener("click", function () {
    state.timeOfDay = state.timeOfDay === "day" ? "sunset" : "day";
    paintSky(state.timeOfDay);
    this.textContent = state.timeOfDay === "day" ? "☀️ День / Закат" : "🌇 Закат Vice City";
  });

  // Street View Panorama Modal
  function showStreetPhoto(idx, openModal) {
    if (!state.streetPhotos.length) return;
    state.currentPhotoIdx = (idx + state.streetPhotos.length) % state.streetPhotos.length;
    const item = state.streetPhotos[state.currentPhotoIdx];
    svTitle.textContent = "📸 " + item.roadName;
    svMeta.textContent = "Панорама #" + (state.currentPhotoIdx + 1) + " из " + state.streetPhotos.length;
    svImage.src = item.file;
    if (openModal) {
      streetModal.classList.remove("hidden");
      auraDrawer.classList.add("hidden");
    }
  }

  document.getElementById("btnToggleStreetModal").addEventListener("click", function () {
    const hidden = streetModal.classList.toggle("hidden");
    this.classList.toggle("active", !hidden);
    if (!hidden) showStreetPhoto(state.currentPhotoIdx, true);
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
      streetModal.classList.add("hidden");
      showBanner("ФАСАДЫ ОБНОВЛЕНЫ", "Панорама улицы Днепра натянута на здания");
    };
    img.src = svImage.src;
  });

  // Enter / Exit Ferrari 458 Italia
  const driveBtnIcon = document.getElementById("driveBtnIcon");
  const driveBtnText = document.getElementById("driveBtnText");

  function toggleDriveMode() {
    if (state.mode === "walk") {
      state.mode = "drive";
      playerGroup.visible = false;
      driveBtnIcon.textContent = "🚶";
      driveBtnText.textContent = "Выйти";
      hudModeLabel.textContent = "🏎️ Ferrari 458";
      showBanner("FERRARI 458 ITALIA", "Гони по проспекту Дмитра Яворницького!");
    } else {
      state.mode = "walk";
      state.player.x = state.car.x - Math.cos(state.car.yaw) * 2.2;
      state.player.z = state.car.z + Math.sin(state.car.yaw) * 2.2;
      state.player.yaw = state.car.yaw;
      playerGroup.visible = true;
      driveBtnIcon.textContent = "🏎️";
      driveBtnText.textContent = "Ferrari";
      hudModeLabel.textContent = "🚶 Пешком";
    }
  }
  document.getElementById("btnDriveToggle").addEventListener("click", toggleDriveMode);

  document.getElementById("btnBoost").addEventListener("click", function () {
    state.player.boostTimer = 2.4;
    showBanner("⚡ НИТРО / СПРИНТ", "Максимальное ускорение!");
  });

  document.getElementById("btnCameraView").addEventListener("click", function () {
    state.cameraPreset = (state.cameraPreset + 1) % 3;
    const names = ["КАМЕРА: GTA V", "КАМЕРА: ОБУВЬ И АУРА", "КАМЕРА: ВЕРТОЛЁТ"];
    showBanner(names[state.cameraPreset], "Ракурс переключён");
  });
  document.getElementById("radarWrap").addEventListener("click", function () {
    state.cameraPreset = state.cameraPreset === 2 ? 0 : 2;
  });

  // Touch Virtual Joystick (Canvas-rendered, setPointerCapture)
  function drawJoystick() {
    const w = joystickCanvas.width;
    const h = joystickCanvas.height;
    const cx = w / 2;
    const cy = h / 2;
    joyCtx.clearRect(0, 0, w, h);

    joyCtx.beginPath();
    joyCtx.arc(cx, cy, 50, 0, Math.PI * 2);
    joyCtx.fillStyle = "rgba(12, 18, 32, 0.62)";
    joyCtx.fill();
    joyCtx.lineWidth = 2;
    joyCtx.strokeStyle = "rgba(255, 255, 255, 0.38)";
    joyCtx.stroke();

    const kx = cx + state.joy.dx * 36;
    const ky = cy + state.joy.dy * 36;
    joyCtx.beginPath();
    joyCtx.arc(kx, ky, 20, 0, Math.PI * 2);
    joyCtx.fillStyle = state.auraColor;
    joyCtx.fill();
    joyCtx.lineWidth = 2.5;
    joyCtx.strokeStyle = "#ffffff";
    joyCtx.stroke();
  }
  drawJoystick();

  function updateJoy(e) {
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
    if (joystickCanvas.setPointerCapture) joystickCanvas.setPointerCapture(e.pointerId);
    updateJoy(e);
  });
  joystickCanvas.addEventListener("pointermove", function (e) {
    if (!state.joy.active || e.pointerId !== state.joy.pointerId) return;
    updateJoy(e);
  });
  function releaseJoy(e) {
    if (e && state.joy.pointerId !== null && e.pointerId !== state.joy.pointerId) return;
    state.joy.active = false;
    state.joy.pointerId = null;
    state.joy.dx = 0;
    state.joy.dy = 0;
    drawJoystick();
  }
  joystickCanvas.addEventListener("pointerup", releaseJoy);
  joystickCanvas.addEventListener("pointercancel", releaseJoy);

  // Touch Camera Orbit on World Canvas
  const camOrbit = { active: false, id: null, x: 0, y: 0 };
  worldCanvas.addEventListener("pointerdown", function (e) {
    camOrbit.active = true;
    camOrbit.id = e.pointerId;
    camOrbit.x = e.clientX;
    camOrbit.y = e.clientY;
  });
  worldCanvas.addEventListener("pointermove", function (e) {
    if (!camOrbit.active || e.pointerId !== camOrbit.id) return;
    const dx = e.clientX - camOrbit.x;
    const dy = e.clientY - camOrbit.y;
    camOrbit.x = e.clientX;
    camOrbit.y = e.clientY;
    state.cameraYaw -= dx * 0.007;
    state.cameraPitch = Math.max(0.08, Math.min(0.85, state.cameraPitch + dy * 0.004));
  });
  window.addEventListener("pointerup", function (e) {
    if (e.pointerId === camOrbit.id) camOrbit.active = false;
  });

  // Keyboard Controls
  window.addEventListener("keydown", function (e) {
    if (document.activeElement === auraTextInput) return;
    state.keys[e.code] = true;
    if (e.code === "KeyF" || e.code === "KeyE") toggleDriveMode();
    if (e.code === "Space") state.player.boostTimer = 2.0;
  });
  window.addEventListener("keyup", function (e) {
    state.keys[e.code] = false;
  });

  // Render GTA V Minimap Radar
  function renderRadar(px, pz, pyaw) {
    const w = radarCanvas.width;
    const h = radarCanvas.height;
    const cx = w / 2;
    const cy = h / 2;
    radarCtx.clearRect(0, 0, w, h);

    radarCtx.save();
    radarCtx.translate(cx, cy);
    radarCtx.rotate(pyaw);

    const scale = 1.1;
    if (state.satelliteImg) {
      radarCtx.drawImage(
        state.satelliteImg,
        (-BOUNDS.widthM / 2 - px) * scale,
        (-BOUNDS.heightM / 2 - pz) * scale,
        BOUNDS.widthM * scale,
        BOUNDS.heightM * scale
      );
    }

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

    state.checkpoints.forEach(function (cp) {
      if (cp.done) return;
      radarCtx.fillStyle = "#facc15";
      radarCtx.beginPath();
      radarCtx.arc((cp.x - px) * scale, (cp.z - pz) * scale, 4, 0, Math.PI * 2);
      radarCtx.fill();
    });

    radarCtx.restore();

    // Player Triangle Cursor
    radarCtx.save();
    radarCtx.translate(cx, cy);
    radarCtx.fillStyle = "#ffffff";
    radarCtx.strokeStyle = "#000000";
    radarCtx.lineWidth = 2;
    radarCtx.beginPath();
    radarCtx.moveTo(0, -7);
    radarCtx.lineTo(5, 6);
    radarCtx.lineTo(0, 3);
    radarCtx.lineTo(-5, 6);
    radarCtx.closePath();
    radarCtx.fill();
    radarCtx.stroke();
    radarCtx.restore();
  }

  // Main 60 FPS Loop
  let lastTime = performance.now();
  function animate(now) {
    requestAnimationFrame(animate);
    const dt = Math.min(0.05, (now - lastTime) / 1000);
    lastTime = now;

    if (state.mixer) state.mixer.update(dt);
    if (state.carouselGroup) state.carouselGroup.rotation.y += dt * 0.45;

    // Update Moving AI Traffic (Dnipro Tram #1 & Delivery Truck) along Dmytro Yavornytskyi Ave
    const aveAngle = -0.68;
    state.traffic.forEach(function (tr) {
      tr.t += tr.speed * dt;
      if (tr.t > 95) tr.t = -95;
      if (tr.t < -95) tr.t = 95;
      const wx = -18 + Math.cos(aveAngle) * tr.laneOffset + Math.sin(aveAngle) * tr.t;
      const wz = -14 - Math.sin(aveAngle) * tr.laneOffset + Math.cos(aveAngle) * tr.t;
      tr.group.position.set(wx, 0, wz);
      tr.group.rotation.y = tr.speed >= 0 ? aveAngle : aveAngle + Math.PI;
    });

    // Input Vector
    let inX = state.joy.dx;
    let inY = state.joy.dy;
    if (state.keys.KeyW || state.keys.ArrowUp) inY -= 1;
    if (state.keys.KeyS || state.keys.ArrowDown) inY += 1;
    if (state.keys.KeyA || state.keys.ArrowLeft) inX -= 1;
    if (state.keys.KeyD || state.keys.ArrowRight) inX += 1;

    const inputMag = Math.min(1, Math.hypot(inX, inY));
    if (state.player.boostTimer > 0) state.player.boostTimer = Math.max(0, state.player.boostTimer - dt);
    const isSprinting = state.player.boostTimer > 0 || inputMag > 0.72;
    const boostMult = state.player.boostTimer > 0 ? 1.85 : 1.0;

    let activeX = state.player.x;
    let activeZ = state.player.z;
    let speedKmh = 0;

    if (state.mode === "walk") {
      const maxWalkSpeed = (isSprinting ? 7.8 : 4.2) * boostMult;
      if (inputMag > 0.06) {
        const moveAngle = Math.atan2(inX, inY) + state.cameraYaw;
        state.player.yaw = moveAngle;
        state.player.speed = inputMag * maxWalkSpeed;
        state.player.x += Math.sin(moveAngle) * state.player.speed * dt;
        state.player.z += Math.cos(moveAngle) * state.player.speed * dt;

        switchCharacterAnim(isSprinting ? "Run" : "Walk");

        state.player.stepAccum += state.player.speed * dt;
        if (state.player.stepAccum > 1.45) {
          state.player.stepAccum = 0;
          spawnFootprint(state.player.x, state.player.z, state.player.yaw);
        }
      } else {
        state.player.speed = 0;
        switchCharacterAnim("Idle");
      }

      state.player.x = Math.max(-108, Math.min(108, state.player.x));
      state.player.z = Math.max(-102, Math.min(102, state.player.z));

      playerGroup.position.set(state.player.x, 0, state.player.z);
      characterHolder.rotation.y = state.player.yaw;
      auraRingMesh.rotation.z -= dt * 0.75;

      activeX = state.player.x;
      activeZ = state.player.z;
      speedKmh = Math.round(state.player.speed * 3.1);
    } else {
      // Ferrari 458 Italia Driving Physics
      const accel = -inY * 28 * boostMult;
      state.car.speed += accel * dt;
      state.car.speed *= Math.pow(0.96, dt * 60);
      state.car.speed = Math.max(-16, Math.min(44 * boostMult, state.car.speed));

      if (Math.abs(state.car.speed) > 0.4) {
        const dir = state.car.speed >= 0 ? 1 : -1;
        state.car.yaw -= inX * 2.1 * dir * dt;
      }
      state.car.x += Math.sin(state.car.yaw) * state.car.speed * dt;
      state.car.z += Math.cos(state.car.yaw) * state.car.speed * dt;
      state.car.x = Math.max(-108, Math.min(108, state.car.x));
      state.car.z = Math.max(-102, Math.min(102, state.car.z));

      state.car.wheels.forEach(function (w) {
        w.rotation.x += state.car.speed * dt * 1.8;
      });

      state.cameraYaw = state.car.yaw + Math.PI;
      activeX = state.car.x;
      activeZ = state.car.z;
      speedKmh = Math.round(Math.abs(state.car.speed) * 3.6);
    }

    carGroup.position.set(state.car.x, 0, state.car.z);
    carGroup.rotation.y = state.car.yaw;

    // Fade out neon sneaker footprints
    for (let i = state.footprints.length - 1; i >= 0; i--) {
      const fp = state.footprints[i];
      fp.age += dt;
      fp.mesh.material.opacity = Math.max(0, 0.75 * (1 - fp.age / 3.0));
      if (fp.age >= 3.0) {
        scene.remove(fp.mesh);
        fp.mesh.material.dispose();
        state.footprints.splice(i, 1);
      }
    }

    // GTA V Camera Framing
    if (state.cameraPreset === 0) {
      const dist = state.mode === "drive" ? 7.8 : 4.8;
      const height = state.mode === "drive" ? 2.4 : 2.1;
      camera.position.set(
        activeX + Math.sin(state.cameraYaw) * dist,
        height + state.cameraPitch * 3.5,
        activeZ + Math.cos(state.cameraYaw) * dist
      );
      camera.lookAt(activeX, state.mode === "drive" ? 1.05 : 1.35, activeZ);
    } else if (state.cameraPreset === 1) {
      // Close-up Sneaker & Aura Inspection Angle
      camera.position.set(
        activeX + Math.sin(state.cameraYaw + 0.5) * 2.6,
        1.1,
        activeZ + Math.cos(state.cameraYaw + 0.5) * 2.6
      );
      camera.lookAt(activeX, 0.85, activeZ);
    } else {
      // Helicopter Overview over Dnipro Center
      camera.position.set(activeX + 18, 48, activeZ + 36);
      camera.lookAt(activeX, 0, activeZ);
    }

    // Checkpoints
    let doneCount = 0;
    checkpointMeshes.forEach(function (item) {
      item.halo.rotation.y += dt * 1.2;
      if (item.cp.done) {
        doneCount++;
        return;
      }
      if (Math.hypot(item.cp.x - activeX, item.cp.z - activeZ) < 4.5) {
        item.cp.done = true;
        item.halo.material.color.setHex(0x22c55e);
        state.cash += 50000;
        hudCash.textContent = "$" + state.cash.toLocaleString("en-US");
        showBanner("MISSION PASSED +$50,000", item.cp.title);
      }
    });
    hudMissionText.textContent =
      doneCount === state.checkpoints.length
        ? "🏆 Все 5 локаций Днепра пройдены!"
        : "Миссия: ЦУМ, Карусель, Європа (" + doneCount + "/5)";

    hudSpeedLabel.textContent = speedKmh + " км/ч";
    renderRadar(activeX, activeZ, state.cameraYaw);
    renderer.render(scene, camera);
  }

  window.addEventListener("resize", function () {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
  });

  window.__BOSSBLOCK_GTA__ = {
    state: state,
    toggleDriveMode: toggleDriveMode,
    applyShoePreset: applyShoePreset,
  };

  requestAnimationFrame(animate);
})();
