#!/usr/bin/env node
/** Validate the four event-type colors in both site themes. */

const fs = require("node:fs");
const path = require("node:path");

const site = fs.readFileSync(path.join(__dirname, "..", "site", "index.html"), "utf8");
const names = ["music", "arts", "sports", "other"];
const blocks = [...site.matchAll(/(?:^|\})\s*([^{}]*)\{([^{}]*--t-music:[^{}]*)\}/gm)];

function paletteFrom(css) {
  const result = {};
  for (const name of names) {
    const match = css.match(new RegExp(`--t-${name}\\s*:\\s*(#[0-9a-f]{6})`, "i"));
    if (!match) throw new Error(`missing --t-${name} in a palette block`);
    result[name] = match[1];
  }
  return result;
}

const palettes = blocks.map((match, index) => ({
  label: index === 0 ? "light" : "dark",
  colors: paletteFrom(match[2]),
}));
if (palettes.length !== 2) throw new Error(`expected light and dark palettes; found ${palettes.length}`);

const simulations = {
  normal: [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
  protanopia: [[0.152286, 1.052583, -0.204868], [0.114503, 0.786281, 0.099216], [-0.003882, -0.048116, 1.051998]],
  deuteranopia: [[0.367322, 0.860646, -0.227968], [0.280085, 0.672501, 0.047413], [-0.011820, 0.042940, 0.968881]],
  tritanopia: [[1.255528, -0.076749, -0.178779], [-0.078411, 0.930809, 0.147602], [0.004733, 0.691367, 0.303900]],
};

const clamp = value => Math.max(0, Math.min(1, value));
const linear = value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
const gamma = value => value <= 0.0031308 ? value * 12.92 : 1.055 * value ** (1 / 2.4) - 0.055;

function simulate(hex, matrix) {
  const rgb = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255).map(linear);
  return matrix.map(row => clamp(gamma(row.reduce((sum, coefficient, i) => sum + coefficient * rgb[i], 0))));
}

function lab(rgb) {
  const [r, g, b] = rgb.map(linear);
  const xyz = [
    (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047,
    0.2126 * r + 0.7152 * g + 0.0722 * b,
    (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883,
  ].map(value => value > 0.008856 ? Math.cbrt(value) : 7.787 * value + 16 / 116);
  return [116 * xyz[1] - 16, 500 * (xyz[0] - xyz[1]), 200 * (xyz[1] - xyz[2])];
}

function distance(a, b) {
  return Math.sqrt(a.reduce((sum, value, i) => sum + (value - b[i]) ** 2, 0));
}

let failed = false;
for (const palette of palettes) {
  console.log(`${palette.label}: ${names.map(name => `${name} ${palette.colors[name]}`).join(", ")}`);
  for (const [vision, matrix] of Object.entries(simulations)) {
    const values = Object.fromEntries(names.map(name => [name, lab(simulate(palette.colors[name], matrix))]));
    let minimum = {distance: Infinity, pair: ""};
    for (let i = 0; i < names.length; i++) {
      for (let j = i + 1; j < names.length; j++) {
        const value = distance(values[names[i]], values[names[j]]);
        if (value < minimum.distance) minimum = {distance: value, pair: `${names[i]}/${names[j]}`};
      }
    }
    const threshold = vision === "normal" ? 15 : 8;
    const ok = minimum.distance >= threshold;
    failed ||= !ok;
    console.log(`  ${ok ? "PASS" : "FAIL"} ${vision.padEnd(13)} minimum ${minimum.distance.toFixed(1)} (${minimum.pair}; threshold ${threshold})`);
  }
}

if (failed) process.exit(1);
