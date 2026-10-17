const { join } = require("./util");
const path = require("path");
function legacyUrl(p) { return join(path.normalize(p)); }
module.exports = { legacyUrl };
