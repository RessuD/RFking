self.onmessage = (e) => {
  const {
    width,
    height,
    bounds,
    chmScale,
    chmOffset,
    dtmScale,
    dtmOffset,
    chmMax,
    chmBuffer,
    dtmBuffer,
    validBuffer,
    clickMerc,
    freqGHz,
    stepMeters,
    displayBoost,
    txPowerDbm,
    rxThresholdDbm,
    txHeightM,
    rxHeightM,
    reliabilityMarginDb,
    analysisSize,
  } = e.data;

  const chm = new Uint16Array(chmBuffer);
  const dtm = new Uint16Array(dtmBuffer);
  const valid = validBuffer ? new Uint8Array(validBuffer) : null;

  const minx = bounds.minx;
  const maxx = bounds.maxx;
  const miny = bounds.miny;
  const maxy = bounds.maxy;
  const dx = (maxx - minx) / width;
  const dy = (maxy - miny) / height;

  const analysisW = analysisSize;
  const analysisH = analysisSize;
  const analysisDx = (maxx - minx) / analysisW;
  const analysisDy = (maxy - miny) / analysisH;

  const fMHz = freqGHz * 1000.0;

  const corners = [
    [minx, miny],
    [minx, maxy],
    [maxx, miny],
    [maxx, maxy],
  ];
  let maxDist = 0;
  for (const [cx, cy] of corners) {
    const d = Math.hypot(cx - clickMerc.x, cy - clickMerc.y);
    if (d > maxDist) maxDist = d;
  }

  const maxA = txPowerDbm - rxThresholdDbm;
  const invMaxA = maxA > 0 ? 1.0 / maxA : 1.0;

  const heightLut = new Float64Array(65536);
  for (let i = 0; i < 65536; i++) {
    const h = i * chmScale + chmOffset;
    heightLut[i] = 0.2 * Math.pow(freqGHz, 0.3) * Math.pow(h, 0.6);
  }

  function sampleGrid(data, x, y, scale, offset) {
    const col = (x - minx) / dx;
    const row = (maxy - y) / dy;

    const c0 = Math.floor(col);
    const r0 = Math.floor(row);
    const c1 = c0 + 1;
    const r1 = r0 + 1;

    if (c0 < 0 || r0 < 0 || c1 >= width || r1 >= height) {
      return null;
    }

    const t = col - c0;
    const u = row - r0;

    const idx00 = r0 * width + c0;
    const idx10 = r0 * width + c1;
    const idx01 = r1 * width + c0;
    const idx11 = r1 * width + c1;

    if (valid && (valid[idx00] !== 1 || valid[idx10] !== 1 || valid[idx01] !== 1 || valid[idx11] !== 1)) {
      return null;
    }

    const v00 = data[idx00] * scale + offset;
    const v10 = data[idx10] * scale + offset;
    const v01 = data[idx01] * scale + offset;
    const v11 = data[idx11] * scale + offset;

    const v0 = v00 + t * (v10 - v00);
    const v1 = v01 + t * (v11 - v01);
    return v0 + u * (v1 - v0);
  }

  function knifeEdgeLoss(v) {
    if (v <= -0.78) return 0;
    return 6.9 + 20 * Math.log10(Math.sqrt((v - 0.1) * (v - 0.1) + 1) + v - 0.1);
  }

  function deygoutLoss(profile) {
    if (profile.length < 3) return 0;

    let maxV = -Infinity;
    let maxIdx = -1;

    const dTotal = profile[profile.length - 1].d;
    const h0 = profile[0].h;
    const h1 = profile[profile.length - 1].h;

    for (let i = 1; i < profile.length - 1; i++) {
      const p = profile[i];
      const d1 = p.d;
      const d2 = dTotal - d1;
      const hLos = h0 + (h1 - h0) * (d1 / dTotal);
      const hExcess = p.h - hLos;
      const lambda = 0.3 / freqGHz;
      const v = hExcess * Math.sqrt((2 / lambda) * (dTotal / (d1 * d2)));
      if (v > maxV) {
        maxV = v;
        maxIdx = i;
      }
    }

    if (maxV <= 0) return 0;

    const lambda = 0.3 / freqGHz;
    const d1 = profile[maxIdx].d;
    const d2 = dTotal - d1;
    const hLos = h0 + (h1 - h0) * (d1 / dTotal);
    const hExcess = profile[maxIdx].h - hLos;
    const vMain = hExcess * Math.sqrt((2 / lambda) * (dTotal / (d1 * d2)));

    const mainLoss = knifeEdgeLoss(vMain);

    const left = profile.slice(0, maxIdx + 1);
    const right = profile.slice(maxIdx);

    const leftLoss = deygoutLoss(left);
    const rightLoss = deygoutLoss(right);

    return mainLoss + leftLoss + rightLoss;
  }

  const data = new Uint8ClampedArray(analysisW * analysisH * 4);
  const stepKm = stepMeters / 1000.0;

  for (let r = 0; r < analysisH; r++) {
    const y = maxy - (r + 0.5) * analysisDy;
    for (let c = 0; c < analysisW; c++) {
      const idx = r * analysisW + c;
      const x = minx + (c + 0.5) * analysisDx;

      const dxp = x - clickMerc.x;
      const dyp = y - clickMerc.y;
      const dist = Math.hypot(dxp, dyp);
      if (dist === 0) {
        const o = idx * 4;
        data[o + 3] = 255;
        continue;
      }

      const steps = Math.max(1, Math.floor(dist / stepMeters));
      const invSteps = 1.0 / steps;
      const stepX = dxp * invSteps;
      const stepY = dyp * invSteps;

      const profile = [];
      let attenFoliage = 0;
      let sx = clickMerc.x;
      let sy = clickMerc.y;

      for (let s = 0; s <= steps; s++) {
        const zTerrain = sampleGrid(dtm, sx, sy, dtmScale, dtmOffset);
        if (zTerrain !== null) {
          const zCanopy = sampleGrid(chm, sx, sy, chmScale, chmOffset) || 0;
          profile.push({
            d: (dist * s) / steps,
            h: zTerrain + (s === 0 ? txHeightM : (s === steps ? rxHeightM : 0)),
          });
          if (s > 0) {
            const aStep = 0.2 * Math.pow(freqGHz, 0.3) * Math.pow(zCanopy, 0.6) * stepKm;
            attenFoliage += aStep;
          }
        }
        sx += stepX;
        sy += stepY;
      }

      if (profile.length < 2) {
        const o = idx * 4;
        data[o + 3] = 0;
        continue;
      }

      const dKm = dist / 1000.0;
      const fspl = 32.44 + 20 * Math.log10(fMHz) + 20 * Math.log10(dKm);
      const diffLoss = deygoutLoss(profile);
      const totalLoss = fspl + diffLoss + attenFoliage + reliabilityMarginDb;

      const rxDbm = txPowerDbm - totalLoss;
      const margin = rxDbm - rxThresholdDbm;

      const tRaw = Math.max(0, Math.min(1, margin * invMaxA * displayBoost));
      const t = Math.pow(tRaw, 0.7);
      const rCol = Math.min(1, 3 * t);
      const gCol = Math.min(1, Math.max(0, 3 * t - 1));
      const bCol = Math.min(1, Math.max(0, 3 * t - 2));
      const alpha = margin >= 0 ? (0.1 + 0.55 * t) : 0.0;

      const o = idx * 4;
      data[o] = Math.round(rCol * 255);
      data[o + 1] = Math.round(gCol * 255);
      data[o + 2] = Math.round(bCol * 255);
      data[o + 3] = Math.round(alpha * 255);
    }

    if (r % 8 === 0) {
      self.postMessage({ type: "progress", value: r / (analysisH - 1) });
    }
  }

  self.postMessage(
    {
      type: "done",
      imageData: data.buffer,
      maxA,
      width: analysisW,
      height: analysisH,
    },
    [data.buffer]
  );
};
