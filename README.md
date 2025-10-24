Repository for my three Home Assistans servers.

I have two houses. The one I live in and a holiday home.

In my house I have on Home Assistant that acts as a web front. I reach this instance through a https-tunnel and this server is behind a firewall on a DMZ.

The "real" Home Assistant sits behind another firewall and they only talk mqtt over SSL.

The Home Assistant in the holiday home only talks to the "real" server at home and only via mqtt over SSL and over a VPN tunnel between the sites.

![Schematic network](https://github.com/AceMoneus/My-Home-Assistant-config-files/blob/main/Network.png)
