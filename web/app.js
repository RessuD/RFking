const DATA_DIR = "data";
let chmData = null;
let dtmData = null;
let meta = null;
let map = null;
let overlay = null;
let lastClick = null;
let clickMarker = null;
let validMask = null;
let worker = null;

const freqSlider = document.getElementById("freq");
const freqValue = document.getElementById("freqValue");
const statusEl = document.getElementById("status");
const legendMaxEl = document.getElementById("legendMax");
const legendMinEl = document.getElementById("legendMin");
const progressBar = document.getElementById("progressBar");
const progressText = document.getElementById("progressText");
const legendTickEls = Array.from(document.querySelectorAll("#legendTicks [data-tick]"));
const deviceSelect = document.getElementById("deviceSelect");
const txPowerInput = document.getElementById("txPower");
const rxSensInput = document.getElementById("rxSens");
const elintSensInput = document.getElementById("elintSens");
const txHeightInput = document.getElementById("txHeight");
const rxHeightInput = document.getElementById("rxHeight");
const reliabilityInput = document.getElementById("reliability");
const reliabilityValue = document.getElementById("reliabilityValue");
const gridSizeSelect = document.getElementById("gridSize");
const useElintInput = document.getElementById("useElint");

const DEVICE_PROFILES = [
  {
    id: "lora_eu868",
    name: "LoRa (EU868)",
    freqGHz: 0.868,
    txPowerDbm: 14,
    rxSensDbm: -137,
  },
  {
    id: "pmr446",
    name: "PMR446",
    freqGHz: 0.446,
    txPowerDbm: 27,
    rxSensDbm: -119,
  },
  {
    id: "hunting_68",
    name: "Metsästysradio 68–72 MHz",
    freqGHz: 0.070,
    txPowerDbm: 37,
    rxSensDbm: -119,
  },
  {
    id: "lv217",
    name: "LV217 / PRC‑77‑tyyppi",
    freqGHz: 0.052,
    txPowerDbm: 33,
    rxSensDbm: -118,
  },
  {
    id: "gsm900",
    name: "GSM900 (UE)",
    freqGHz: 0.9,
    txPowerDbm: 33,
    rxSensDbm: -102,
  },
  {
    id: "dcs1800",
    name: "DCS1800 (UE)",
    freqGHz: 1.8,
    txPowerDbm: 30,
    rxSensDbm: -102,
  },
  {
    id: "umts2100",
    name: "UMTS2100 (UE)",
    freqGHz: 2.1,
    txPowerDbm: 24,
    rxSensDbm: -106.7,
  },
  {
    id: "lte800",
    name: "LTE 800 (UE, 10 MHz)",
    freqGHz: 0.8,
    txPowerDbm: 23,
    rxSensDbm: -97,
  },
  {
    id: "lte2600",
    name: "LTE 2600 (UE, 10 MHz)",
    freqGHz: 2.6,
    txPowerDbm: 23,
    rxSensDbm: -97,
  },
  {
    id: "nr700",
    name: "5G NR 700 (UE, 20 MHz)",
    freqGHz: 0.7,
    txPowerDbm: 23,
    rxSensDbm: -93.8,
  },
  {
    id: "nr3500",
    name: "5G NR 3.5 (UE, 20 MHz)",
    freqGHz: 3.5,
    txPowerDbm: 23,
    rxSensDbm: -93.8,
  },
  {
    id: "wifi24",
    name: "Wi‑Fi 2.4 (alhaiset nopeudet)",
    freqGHz: 2.437,
    txPowerDbm: 20,
    rxSensDbm: -90,
  },
  {
    id: "wifi5",
    name: "Wi‑Fi 5 GHz (alhaiset nopeudet)",
    freqGHz: 5.5,
    txPowerDbm: 30,
    rxSensDbm: -92,
  },
];

function lonLatToMerc(lon, lat) {
  const R = 6378137.0;
  const x = R * lon * Math.PI / 180.0;
  const y = R * Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI / 180.0) / 2));
  return { x, y };
}

function hotColor(t) {
  const r = Math.min(1, 3 * t);
  const g = Math.min(1, Math.max(0, 3 * t - 1));
  const b = Math.min(1, Math.max(0, 3 * t - 2));
  return [r, g, b];
}

function drawLegend() {
  const canvas = document.getElementById("legendCanvas");
  const ctx = canvas.getContext("2d");
  const h = canvas.height;
  const w = canvas.width;

  for (let y = 0; y < h; y++) {
    const t = 1 - y / (h - 1);
    const [r, g, b] = hotColor(t);
    ctx.fillStyle = `rgb(${Math.round(r * 255)}, ${Math.round(g * 255)}, ${Math.round(b * 255)})`;
    ctx.fillRect(0, y, w, 1);
  }
}

function updateLegendTicks(maxA) {
  const ticks = [1, 0.75, 0.5, 0.25, 0];
  for (let i = 0; i < legendTickEls.length; i++) {
    const t = ticks[i];
    const val = maxA * t;
    legendTickEls[i].textContent = val.toFixed(1);
  }
}

function updateFreqLabel() {
  const f = parseFloat(freqSlider.value);
  freqValue.textContent = `${f.toFixed(3)} GHz`;
}

function updateReliabilityLabel() {
  reliabilityValue.textContent = `${reliabilityInput.value}%`;
}

function reliabilityMarginDb(pct) {
  const clamped = Math.max(50, Math.min(99, pct));
  return (clamped - 50) * (20 / 49);
}

function setDevice(profile) {
  freqSlider.value = profile.freqGHz.toFixed(3);
  updateFreqLabel();
  txPowerInput.value = profile.txPowerDbm.toFixed(1);
  rxSensInput.value = profile.rxSensDbm.toFixed(1);
  elintSensInput.value = profile.rxSensDbm.toFixed(1);
}

function computeCoverage(latlng) {
  if (!meta || !chmData || !dtmData) return;

  statusEl.textContent = "Lasketaan...";
  progressBar.style.width = "0%";
  progressText.textContent = "0%";

  const f = parseFloat(freqSlider.value);
  const clickMerc = lonLatToMerc(latlng.lng, latlng.lat);

  if (!worker) {
    worker = new Worker("worker.js");
    worker.onmessage = (ev) => {
      if (ev.data.type === "progress") {
        const pct = Math.round(ev.data.value * 100);
        progressBar.style.width = `${pct}%`;
        progressText.textContent = `${pct}%`;
        return;
      }
      if (ev.data.type === "done") {
        const { imageData, maxA, width, height } = ev.data;
        const data = new Uint8ClampedArray(imageData);

        const canvas = document.createElement("canvas");
        canvas.width = width;
        canvas.height = height;
        const ctx = canvas.getContext("2d");
        const img = new ImageData(data, width, height);
        ctx.putImageData(img, 0, 0);

        const bounds = [
          [meta.bounds_wgs84.lat_min, meta.bounds_wgs84.lon_min],
          [meta.bounds_wgs84.lat_max, meta.bounds_wgs84.lon_max],
        ];

        if (overlay) {
          map.removeLayer(overlay);
        }
        overlay = L.imageOverlay(canvas.toDataURL(), bounds, { opacity: 1.0 });
        overlay.addTo(map);

        legendMaxEl.textContent = maxA.toFixed(2);
        legendMinEl.textContent = "0";
        updateLegendTicks(maxA);
        statusEl.textContent = `Valmis (${f.toFixed(3)} GHz)`;
        progressBar.style.width = "100%";
        progressText.textContent = "100%";
      }
    };
  }

  const useElint = useElintInput.checked;
  const rxThreshold = useElint
    ? parseFloat(elintSensInput.value)
    : parseFloat(rxSensInput.value);

  const reliabilityMargin = reliabilityMarginDb(parseFloat(reliabilityInput.value));

  worker.postMessage({
    width: meta.width,
    height: meta.height,
    bounds: meta.bounds,
    chmScale: meta.chm_scale,
    chmOffset: meta.chm_offset,
    dtmScale: meta.dtm_scale,
    dtmOffset: meta.dtm_offset,
    chmMax: meta.chm_max_height,
    chmBuffer: chmData.buffer,
    dtmBuffer: dtmData.buffer,
    validBuffer: validMask ? validMask.buffer : null,
    clickMerc,
    freqGHz: f,
    stepMeters: 50,
    displayBoost: 4.0,
    txPowerDbm: parseFloat(txPowerInput.value),
    rxThresholdDbm: rxThreshold,
    txHeightM: parseFloat(txHeightInput.value),
    rxHeightM: parseFloat(rxHeightInput.value),
    reliabilityMarginDb: reliabilityMargin,
    analysisSize: parseInt(gridSizeSelect.value, 10),
  });
}

async function loadData() {
  const metaResp = await fetch(`${DATA_DIR}/metadata.json`);
  meta = await metaResp.json();

  const binResp = await fetch(`${DATA_DIR}/chm_u16.bin`);
  const buffer = await binResp.arrayBuffer();
  chmData = new Uint16Array(buffer);

  const dtmResp = await fetch(`${DATA_DIR}/dtm_u16.bin`);
  const dtmBuf = await dtmResp.arrayBuffer();
  dtmData = new Uint16Array(dtmBuf);

  try {
    const maskResp = await fetch(`${DATA_DIR}/valid_u8.bin`);
    if (maskResp.ok) {
      const maskBuf = await maskResp.arrayBuffer();
      validMask = new Uint8Array(maskBuf);
    }
  } catch (_) {
    validMask = null;
  }

  for (const profile of DEVICE_PROFILES) {
    const opt = document.createElement("option");
    opt.value = profile.id;
    opt.textContent = profile.name;
    deviceSelect.appendChild(opt);
  }
  setDevice(DEVICE_PROFILES[0]);

  drawLegend();

  const centerLat = (meta.bounds_wgs84.lat_min + meta.bounds_wgs84.lat_max) / 2;
  const centerLon = (meta.bounds_wgs84.lon_min + meta.bounds_wgs84.lon_max) / 2;

  map = L.map("map").setView([centerLat, centerLon], 14);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap",
  }).addTo(map);

  const bounds = [
    [meta.bounds_wgs84.lat_min, meta.bounds_wgs84.lon_min],
    [meta.bounds_wgs84.lat_max, meta.bounds_wgs84.lon_max],
  ];
  map.fitBounds(bounds);

  map.on("click", (e) => {
    lastClick = e.latlng;
    if (!clickMarker) {
      clickMarker = L.circleMarker(e.latlng, {
        radius: 6,
        color: "#f9b233",
        weight: 2,
        fillColor: "#f9b233",
        fillOpacity: 0.8,
      }).addTo(map);
    } else {
      clickMarker.setLatLng(e.latlng);
    }
    computeCoverage(e.latlng);
  });

  freqSlider.addEventListener("input", () => {
    updateFreqLabel();
  });

  deviceSelect.addEventListener("change", () => {
    const profile = DEVICE_PROFILES.find((p) => p.id === deviceSelect.value);
    if (profile) {
      setDevice(profile);
      if (lastClick) {
        computeCoverage(lastClick);
      }
    }
  });

  reliabilityInput.addEventListener("input", updateReliabilityLabel);
  updateReliabilityLabel();

  const recomputeOnInput = [freqSlider, reliabilityInput];
  const recomputeOnChange = [gridSizeSelect, useElintInput, deviceSelect];
  const recomputeOnEnter = [txPowerInput, rxSensInput, elintSensInput, txHeightInput, rxHeightInput];

  function triggerCompute() {
    if (!lastClick) {
      lastClick = map.getCenter();
      if (!clickMarker) {
        clickMarker = L.circleMarker(lastClick, {
          radius: 6,
          color: "#f9b233",
          weight: 2,
          fillColor: "#f9b233",
          fillOpacity: 0.8,
        }).addTo(map);
      } else {
        clickMarker.setLatLng(lastClick);
      }
    }
    computeCoverage(lastClick);
  }

  for (const el of recomputeOnInput) {
    el.addEventListener("input", triggerCompute);
  }

  for (const el of recomputeOnChange) {
    el.addEventListener("change", triggerCompute);
  }

  for (const el of recomputeOnEnter) {
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        triggerCompute();
      }
    });
  }

  updateFreqLabel();
}

loadData().catch((err) => {
  console.error("Failed to load data", err);
});
