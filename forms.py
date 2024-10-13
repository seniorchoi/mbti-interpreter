from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed, FileRequired

class ImageUploadForm(FlaskForm):
    image = FileField('Upload your MBTI Image', validators=[
        FileRequired(),
        FileAllowed(['jpg', 'jpeg', 'png'], 'Images only!')
    ])
