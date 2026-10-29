/* eslint-disable @typescript-eslint/no-require-imports -- NODE_OPTIONS preloads this compatibility shim as CommonJS */
const fs = require("node:fs");
const fsPromises = require("node:fs/promises");

const PATCHED = Symbol.for("towngas.node24.windows.readlink.compat");
if (process.platform === "win32" && !fsPromises[PATCHED]) {
  const normalize = (error, target) => {
    if (error?.code === "EISDIR") {
      try { if (!fs.lstatSync(target).isSymbolicLink()) error.code = "EINVAL"; } catch { /* preserve original */ }
    }
    return error;
  };
  const originalReadlink = fsPromises.readlink.bind(fsPromises);
  const compatibleReadlink = async (target, ...args) => {
    try {
      return await originalReadlink(target, ...args);
    } catch (error) {
      throw normalize(error, target);
    }
  };
  const originalReadlinkCallback = fs.readlink.bind(fs);
  const compatibleReadlinkCallback = (target, ...args) => {
    const callback = args.pop();
    return originalReadlinkCallback(target, ...args, (error, link) => callback(error ? normalize(error, target) : null, link));
  };
  const originalReadlinkSync = fs.readlinkSync.bind(fs);
  const compatibleReadlinkSync = (target, ...args) => {
    try { return originalReadlinkSync(target, ...args); } catch (error) { throw normalize(error, target); }
  };
  fsPromises.readlink = compatibleReadlink;
  fs.promises.readlink = compatibleReadlink;
  fs.readlink = compatibleReadlinkCallback;
  fs.readlinkSync = compatibleReadlinkSync;
  fsPromises[PATCHED] = true;
}
