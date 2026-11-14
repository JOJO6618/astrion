const { randomUUID } = require('node:crypto');

const buildId = process.env.ASTRION_BUILD_ID || randomUUID();
if (!/^[A-Za-z0-9._+-]{1,128}$/.test(buildId)) throw new Error('Invalid ASTRION_BUILD_ID');
module.exports = buildId;
