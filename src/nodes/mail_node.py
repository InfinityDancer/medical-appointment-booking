import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

def send_mail_node(state):
    """
    Send Mail Node.
    - state: workflow state dict (must contain 'to_email', 'subject', 'body')
    - smtp_config: dict with SMTP credentials
    """

    smtp_config = {
    "host": "smtp.gmail.com",
    "port": 587,
    "username": "luffy.xd32@gmail.com",
    "password": "jeye nejo hkac wtoz"
    }
    try:
        # to_email = state.get("to_email")
        # subject = state.get("subject")
        # body = state.get("body")

        to_email = "neha.harchandani@snaptechproject.com"
        subject = "test mail here"
        body = f"""Hello Boss,

        Unfortunately, there was a problem with one of the bot agents for the number: {state["graph_state"].get("sender", "Unknown number")}

        Original User Message: {state["graph_state"].get("whatsapp_message", "User message not available")}

        I already informed the client that I'm going to review the issue. Please check the execution on your computer 
        and respond to the client.

        Sorry for the inconvenience.
        """

        if not to_email or not subject or not body:
            raise ValueError("Missing email fields in state")

        # Create the email
        msg = MIMEMultipart()
        msg["From"] = smtp_config["username"]
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        # Connect to SMTP server
        server = smtplib.SMTP(smtp_config["host"], smtp_config["port"])
        server.starttls()
        server.login(smtp_config["username"], smtp_config["password"])
        server.sendmail(smtp_config["username"], to_email, msg.as_string())
        server.quit()

        print("succesfully sended mail")
        return
    
    except Exception as e:
        print("failed to send a mail",e)
        return

    #     # Save result back in state
    #     state["mail_output"] = {"status": "success", "to": to_email}
    #     return state

    # except Exception as e:
    #     state["mail_output"] = {"status": "failed", "error": str(e)}
    #     return state

