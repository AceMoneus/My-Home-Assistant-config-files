## Repository for My Three Home Assistant Servers

I manage Home Assistant across two properties: my primary residence and a holiday home.

### 🏠 Main House Setup
- One Home Assistant instance acts as a web front-end.
- It’s accessed via an HTTPS tunnel and resides in a DMZ behind a firewall.
- The “real” Home Assistant server is located deeper in the network, behind another firewall.
- Communication between the front-end and the real server is handled exclusively via MQTT over TLS.

### 🏡 Holiday Home Setup
- The Home Assistant instance here communicates only with the main server at home.
- This connection uses MQTT over TLS and a VPN tunnel between the sites.

### 🔄 Communication & Independence
- Both main Home Assistant instances are fully independent in terms of automations and configurations.
- MQTT is used to exchange commands, sensor data, and telemetry between them.
- The external-facing Home Assistant provides dashboards but has limited control over the main servers—only via a few configured MQTT topics.

### 📁 Status
As of November 2025, I haven’t published all configuration files yet.


![Schematic network](https://github.com/AceMoneus/My-Home-Assistant-config-files/blob/main/readme-related/Network.png)
