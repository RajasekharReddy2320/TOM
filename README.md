This is the Agent that reads your google mails from the second you start that and it reads every message from the start.
It will send you a quick summary in the slack message.
If there is an important thing then it automatically drafts a mail to them. 

Architecture :
The information present in the mail will be sent to Gemini Via API Key. 
Gemini reads the message and sends the summary to Slack 
If Gemini thinks that this message is important based on custom instructions for the important messages it automatically drafts reply in gmail.
