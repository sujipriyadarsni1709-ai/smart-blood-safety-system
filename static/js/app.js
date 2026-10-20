
document.addEventListener("DOMContentLoaded", () => {
  const verifyForm = document.getElementById("verify-form");

  if (verifyForm) {
    verifyForm.addEventListener("submit", (event) => {
      const patient = document.getElementById("patient_code").value;
      const unit = document.getElementById("unit_code").value;

      if (!patient || !unit) {
        event.preventDefault();
        window.alert("Select both a demo patient and a blood unit.");
      }
    });
  }

  document.querySelectorAll(".flash").forEach((message) => {
    message.setAttribute("aria-live", "polite");
  });
});