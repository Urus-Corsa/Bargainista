console.log('Hello World from index.js file, refrenced in src attribute of script element')
console.log("Here is 2+2 = "+performSum(2,2))

let devName, yearsOfExperience = 2;
devName = 'Developer: Sina Kalantar'
const age = 25;
console.log(devName);
console.log(`Age is ${age}, and has ${yearsOfExperience} years of experience`)

let person = getPersonObject('Jack', 23)
console.log(`This (person = ${person}) is an ${typeof(person)}.\nIts name attribute value is ${person['name']} & his age is ${person.age}.`)


function performSum(a, b) {
    return String(a+b);
}

function getPersonObject(name, age){
    return {
        'name': name,
        'age': age
    }
}