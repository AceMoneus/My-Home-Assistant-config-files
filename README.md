Repository for my three Home Assistans servers.

I have two houses: the one I live in and a holiday home.

In my main house, I run one Home Assistant instance that acts as a web front. I access this instance through an HTTPS tunnel; it is located behind a firewall in a DMZ.

The “real” Home Assistant sits behind another firewall, and they communicate only via MQTT over SSL.

The Home Assistant in the holiday home communicates exclusively with the “real” server at home, using MQTT over SSL and a VPN tunnel between the sites.

The two main Home Assistant instances at the two sites are independent of each other regarding automations and configuration. However, I use MQTT to send commands, sensor data, and telemetry between them. The external Home Assistant only provides a dashboard and cannot control the main instances, except through a few MQTT topics I have configured.

As of now (october 2025) I hav not yet published all config files, yet...

![Schematic network](https://github.com/AceMoneus/My-Home-Assistant-config-files/blob/main/readme-related/Network.png)
