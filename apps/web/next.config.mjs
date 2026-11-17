/** API requests from the Vercel site are proxied to the secure persistent host.
 * No Gateway API key or studio password is ever included in Vercel's env.
 */
const target = (process.env.STUDIO_ORIGIN || 'http://127.0.0.1:8000').replace(/\/$/, '');
export default {
  async rewrites() { return [{source:'/api/studio/:path*',destination:`${target}/api/studio/:path*`}]; },
  poweredByHeader: false,
};
