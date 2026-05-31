(() => {
  const scanner = document.querySelector("#scanner");
  const video = document.querySelector("#scanner-video");
  const status = document.querySelector("#scanner-status");
  const stopButton = document.querySelector("[data-scan-stop]");
  let stream = null;
  let detector = null;
  let activeTarget = null;
  let active = false;

  async function stopScanner() {
    active = false;
    if (stream) {
      stream.getTracks().forEach((track) => track.stop());
      stream = null;
    }
    if (scanner) {
      scanner.hidden = true;
    }
    if (video) {
      video.srcObject = null;
    }
  }

  async function scanLoop() {
    if (!active || !detector || !video || video.readyState < 2) {
      if (active) {
        window.requestAnimationFrame(scanLoop);
      }
      return;
    }

    try {
      const codes = await detector.detect(video);
      if (codes.length > 0) {
        const value = codes[0].rawValue || "";
        if (activeTarget) {
          activeTarget.value = value;
          activeTarget.dispatchEvent(new Event("change", { bubbles: true }));
        }
        await stopScanner();
        return;
      }
    } catch (error) {
      if (status) {
        status.textContent = "Scanner paused. Paste the QR value instead.";
      }
    }

    if (active) {
      window.requestAnimationFrame(scanLoop);
    }
  }

  async function startScanner(targetId) {
    activeTarget = document.getElementById(targetId);
    if (!activeTarget || !scanner || !video || !status) {
      return;
    }

    if (!("BarcodeDetector" in window)) {
      status.textContent = "Camera scanning is not available in this browser.";
      scanner.hidden = false;
      return;
    }

    try {
      detector = new window.BarcodeDetector({ formats: ["qr_code"] });
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "environment" },
        audio: false,
      });
      video.srcObject = stream;
      scanner.hidden = false;
      active = true;
      status.textContent = "Looking for QR code...";
      window.requestAnimationFrame(scanLoop);
    } catch (error) {
      status.textContent = "Camera permission was not granted.";
      scanner.hidden = false;
    }
  }

  document.querySelectorAll("[data-scan-target]").forEach((button) => {
    button.addEventListener("click", () => startScanner(button.dataset.scanTarget));
  });

  if (stopButton) {
    stopButton.addEventListener("click", stopScanner);
  }

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && scanner && !scanner.hidden) {
      stopScanner();
    }
  });
})();
