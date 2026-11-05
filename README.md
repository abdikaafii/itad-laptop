# itad-laptop
## Sending reports to SealHub

When a report is saved, LaptopSeal also sends it to the SealHub portal (the report always stays on the USB first).

Setup (once, on the Ventoy USB): put a file called `sealhub.conf` in the same place as `technicians.txt`:

```
{
  "url": "https://sealhub.onrender.com",
  "token": "sh_paste-the-token-from-SealHub-Settings-here"
}
```

- No network? The report stays queued on the USB (`...sealhub.json`) and goes out the next time a report is finished with a network connection. A `.sent` file next to it means it was sent.
- Sending the same report twice never makes a duplicate on SealHub.
- Check the connection by hand on the boot USB: `python3 /opt/itad-laptop/sealhub_upload.py --check`
- The token lives only on the USB, never in this repo.
