#!/usr/bin/env node

import { promises as fs } from "fs";
import path from "path";
import process from "process";
import { webcrypto } from "crypto";

const { subtle } = webcrypto;
const encoder = new TextEncoder();

const DEFAULT_APP_HTML = "src/nanogram.app.html";
const DEFAULT_BUNDLE_OUT = "build/public/site.bundle.json";
const DEFAULT_INCLUDES = ["src/themes/default/css/nanogram.css", "build/assets"];
const DEFAULT_ITERATIONS = 250000;
const DEFAULT_OBJS_DIR = "build/public/objs";

function usage() {
  console.log(
    [
      "Usage:",
      "  node src/scripts/build_protected_bundle.mjs --password <password> [options]",
      "",
      "Options:",
      `  --app-html <path>         App HTML to encrypt (default: ${DEFAULT_APP_HTML})`,
      `  --bundle-out <path>       Output encrypted bundle path (default: ${DEFAULT_BUNDLE_OUT})`,
      `  --objs-dir <path>         Output directory for per-file encrypted JSON objects (default: ${DEFAULT_OBJS_DIR})`,
      `  --iterations <n>          PBKDF2 iterations (default: ${DEFAULT_ITERATIONS})`,
      "  --include <path>          Extra file/dir to include (repeatable)",
      "  --no-default-includes     Do not auto-include src/themes/default/css/nanogram.css and build/assets",
      "  -h, --help                Show this help",
    ].join("\n")
  );
}

function toPosixRelative(targetPath) {
  const resolved = path.resolve(targetPath);
  const relative = path.relative(process.cwd(), resolved);
  return relative.split(path.sep).join("/");
}

function base64FromBytes(bytes) {
  return Buffer.from(bytes).toString("base64");
}

function hexFromArrayBuffer(buffer) {
  return Array.from(new Uint8Array(buffer))
    .map((n) => n.toString(16).padStart(2, "0"))
    .join("");
}

async function sha256Hex(text) {
  return hexFromArrayBuffer(await subtle.digest("SHA-256", encoder.encode(text)));
}

async function sha256Bytes(bytes) {
  return new Uint8Array(await subtle.digest("SHA-256", bytes));
}

async function deriveDeterministicSalt(password, iterations) {
  const material = encoder.encode(`nanogram/salt/v1\0${password}\0${iterations}`);
  const digest = await sha256Bytes(material);
  return digest.slice(0, 16);
}

async function deriveDeterministicIv(relPath, plainBytes) {
  const plainHash = await sha256Bytes(plainBytes);
  const prefix = encoder.encode(`nanogram/iv/v1\0${relPath}\0`);
  const input = new Uint8Array(prefix.length + plainHash.length);
  input.set(prefix, 0);
  input.set(plainHash, prefix.length);
  const digest = await sha256Bytes(input);
  return digest.slice(0, 12);
}

function parseArgs(argv) {
  const args = {
    password: "",
    appHtml: DEFAULT_APP_HTML,
    bundleOut: DEFAULT_BUNDLE_OUT,
    objsDir: DEFAULT_OBJS_DIR,
    iterations: DEFAULT_ITERATIONS,
    includes: [],
    useDefaultIncludes: true,
  };

  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (token === "-h" || token === "--help") {
      usage();
      process.exit(0);
    }
    if (token === "--password") {
      args.password = argv[i + 1] || "";
      i += 1;
      continue;
    }
    if (token === "--app-html") {
      args.appHtml = argv[i + 1] || "";
      i += 1;
      continue;
    }
    if (token === "--bundle-out") {
      args.bundleOut = argv[i + 1] || "";
      i += 1;
      continue;
    }
    if (token === "--objs-dir" || token === "--ciphertexts-dir") {
      args.objsDir = argv[i + 1] || "";
      i += 1;
      continue;
    }
    if (token === "--iterations") {
      const parsed = Number(argv[i + 1]);
      if (!Number.isFinite(parsed) || parsed < 1000) {
        throw new Error("--iterations must be a number >= 1000");
      }
      args.iterations = Math.floor(parsed);
      i += 1;
      continue;
    }
    if (token === "--include") {
      const value = argv[i + 1] || "";
      if (!value) {
        throw new Error("--include requires a path");
      }
      args.includes.push(value);
      i += 1;
      continue;
    }
    if (token === "--no-default-includes") {
      args.useDefaultIncludes = false;
      continue;
    }
    throw new Error(`Unknown option: ${token}`);
  }

  if (!args.password) {
    throw new Error("--password is required");
  }

  if (!args.appHtml) {
    throw new Error("--app-html is required");
  }

  if (!args.bundleOut) {
    throw new Error("--bundle-out is required");
  }

  if (!args.objsDir) {
    throw new Error("--objs-dir is required");
  }

  return args;
}

async function pathExists(p) {
  try {
    await fs.access(p);
    return true;
  } catch {
    return false;
  }
}

async function walkFiles(entryPath, outSet) {
  const stat = await fs.stat(entryPath);
  if (stat.isFile()) {
    outSet.add(toPosixRelative(entryPath));
    return;
  }
  if (!stat.isDirectory()) {
    return;
  }

  const dirEntries = await fs.readdir(entryPath, { withFileTypes: true });
  for (const dirent of dirEntries) {
    await walkFiles(path.join(entryPath, dirent.name), outSet);
  }
}

function mimeForPath(filePath) {
  const ext = path.extname(filePath).toLowerCase();
  if (ext === ".html") return "text/html";
  if (ext === ".css") return "text/css";
  if (ext === ".js" || ext === ".mjs") return "application/javascript";
  if (ext === ".json") return "application/json";
  if (ext === ".svg") return "image/svg+xml";
  if (ext === ".png") return "image/png";
  if (ext === ".jpg" || ext === ".jpeg") return "image/jpeg";
  if (ext === ".webp") return "image/webp";
  if (ext === ".gif") return "image/gif";
  if (ext === ".mp4") return "video/mp4";
  if (ext === ".mov") return "video/quicktime";
  if (ext === ".webm") return "video/webm";
  return "application/octet-stream";
}

async function deriveEncryptionKey(password, salt, iterations) {
  const keyMaterial = await subtle.importKey(
    "raw",
    encoder.encode(password),
    { name: "PBKDF2" },
    false,
    ["deriveKey"]
  );
  return subtle.deriveKey(
    {
      name: "PBKDF2",
      hash: "SHA-256",
      salt,
      iterations,
    },
    keyMaterial,
    { name: "AES-GCM", length: 256 },
    false,
    ["encrypt"]
  );
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const appHtmlRel = toPosixRelative(args.appHtml);
  const bundleOutRel = toPosixRelative(args.bundleOut);
  const bundleOutAbs = path.resolve(args.bundleOut);
  const objsDirRel = toPosixRelative(args.objsDir);
  const objsDirAbs = path.resolve(args.objsDir);
  const bundleDirRel = path.posix.dirname(bundleOutRel);

  const includeRoots = [];
  includeRoots.push(args.appHtml);
  if (args.useDefaultIncludes) {
    includeRoots.push(...DEFAULT_INCLUDES);
  }
  includeRoots.push(...args.includes);

  const fileSet = new Set();
  for (const root of includeRoots) {
    const abs = path.resolve(root);
    if (!(await pathExists(abs))) {
      console.warn(`Skipping missing path: ${root}`);
      continue;
    }
    await walkFiles(abs, fileSet);
  }

  fileSet.delete(bundleOutRel);
  fileSet.delete(toPosixRelative(`${bundleOutAbs}.tmp`));
  for (const candidate of Array.from(fileSet)) {
    if (candidate === objsDirRel || candidate.startsWith(`${objsDirRel}/`)) {
      fileSet.delete(candidate);
    }
  }

  if (!fileSet.has(appHtmlRel)) {
    throw new Error(`App HTML file was not included: ${appHtmlRel}`);
  }

  const sortedFiles = Array.from(fileSet).sort();
  if (!sortedFiles.length) {
    throw new Error("No files to encrypt");
  }

  const salt = await deriveDeterministicSalt(args.password, args.iterations);
  const key = await deriveEncryptionKey(args.password, salt, args.iterations);
  const passwordHashHex = hexFromArrayBuffer(
    await subtle.digest("SHA-256", encoder.encode(args.password))
  );

  let encryptedCount = 0;
  let totalPlainBytes = 0;
  const bundleMeta = {
    version: 1,
    created_at_utc: "1970-01-01T00:00:00.000Z",
    app_html_path: appHtmlRel,
    password_hash_sha256: passwordHashHex,
    kdf: {
      algorithm: "PBKDF2",
      hash: "SHA-256",
      iterations: args.iterations,
      salt_b64: base64FromBytes(salt),
    },
    cipher: {
      algorithm: "AES-GCM",
      iv_bytes: 12,
      tag_bytes: 16,
    },
    storage: {
      ciphertext_container: "json_per_file",
      objs_dir: path.posix.relative(bundleDirRel, objsDirRel) || ".",
      ciphertext_field: "ciphertext_b64",
      entry_field: "ciphertext_json",
    }
  };

  await fs.mkdir(path.dirname(bundleOutAbs), { recursive: true });
  await fs.mkdir(objsDirAbs, { recursive: true });
  const tmpBundleOutAbs = `${bundleOutAbs}.tmp`;
  const bundleHandle = await fs.open(tmpBundleOutAbs, "w");
  try {
    const metaJson = JSON.stringify(bundleMeta);
    await bundleHandle.write(`${metaJson.slice(0, -1)},"files":[`);

    const makeObjFileName = async (relPath) => `${await sha256Hex(relPath)}.json`;

    for (let i = 0; i < sortedFiles.length; i += 1) {
      const relPath = sortedFiles[i];
      const absPath = path.resolve(relPath);
      const plain = await fs.readFile(absPath);
      totalPlainBytes += plain.byteLength;

      const iv = await deriveDeterministicIv(relPath, plain);
      const encrypted = await subtle.encrypt(
        { name: "AES-GCM", iv },
        key,
        plain
      );

      const entry = {
        path: relPath,
        mime: mimeForPath(relPath),
        iv_b64: base64FromBytes(iv),
      };
      const ciphertextFileName = await makeObjFileName(relPath);
      const ciphertextFileAbs = path.join(objsDirAbs, ciphertextFileName);
      const ciphertextFileRel = toPosixRelative(ciphertextFileAbs);
      const ciphertextFileClientRel = path.posix.relative(bundleDirRel, ciphertextFileRel) || ciphertextFileName;
      const ciphertextPayload = {
        ciphertext_b64: base64FromBytes(new Uint8Array(encrypted)),
      };
      await fs.writeFile(
        ciphertextFileAbs,
        JSON.stringify(ciphertextPayload),
        "utf8"
      );
      entry.ciphertext_json = ciphertextFileClientRel;

      if (i > 0) {
        await bundleHandle.write(",");
      }
      await bundleHandle.write(JSON.stringify(entry));
      encryptedCount += 1;
    }

    await bundleHandle.write("]}");
  } finally {
    await bundleHandle.close();
  }
  await fs.rename(tmpBundleOutAbs, bundleOutAbs);

  console.log(`Encrypted files: ${encryptedCount}`);
  console.log(`Total plaintext bytes: ${totalPlainBytes}`);
  console.log(`Bundle: ${bundleOutRel}`);
  console.log(`Encrypted objects dir: ${objsDirRel}`);
  console.log(`App HTML path in bundle: ${appHtmlRel}`);
}

main().catch((error) => {
  console.error(`Error: ${error instanceof Error ? error.message : String(error)}`);
  process.exit(1);
});
