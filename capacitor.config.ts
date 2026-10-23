import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.dowflow.mobile',
  appName: 'DowFlow',
  webDir: 'www',
  server: process.env.DOWFLOW_MOBILE_URL
    ? { url: process.env.DOWFLOW_MOBILE_URL, cleartext: false }
    : undefined,
};

export default config;
